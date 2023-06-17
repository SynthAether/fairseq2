# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from dataclasses import dataclass
from typing import Optional

from fairseq2.models.transformer import (
    TransformerEmbeddingFrontend,
    TransformerFrontend,
    TransformerModel,
)
from fairseq2.models.utils.arch import ArchitectureRegistry
from fairseq2.nn.embedding import Embedding
from fairseq2.nn.position_encoder import SinusoidalPositionEncoder
from fairseq2.nn.projection import TiedProjection
from fairseq2.nn.transformer import (
    FeedForwardNetwork,
    MultiheadAttention,
    StandardFeedForwardNetwork,
    StandardMultiheadAttention,
    StandardTransformerDecoder,
    StandardTransformerDecoderLayer,
    StandardTransformerEncoder,
    StandardTransformerEncoderLayer,
    TransformerDecoder,
    TransformerDecoderLayer,
    TransformerEncoder,
    TransformerEncoderLayer,
    TransformerNormOrder,
    get_default_sdpa,
)
from fairseq2.typing import DataType, Device


@dataclass
class NllbConfig:
    """Holds the configuration of an NLLB model."""

    model_dim: int
    """The dimensionality of the model."""

    max_seq_len: int
    """The expected maximum sequence length."""

    vocabulary_size: int
    """The size of the vocabulary."""

    pad_idx: int
    """The index of the pad symbol in the vocabulary."""

    num_encoder_layers: int
    """The number of Transformer encoder layers."""

    num_decoder_layers: int
    """The number of Transformer decoder layers."""

    num_encoder_attn_heads: int
    """The number of attention heads in Transformer encoder layers."""

    num_decoder_attn_heads: int
    """The number of attention heads in Transformer decoder layers."""

    ffn_inner_dim: int
    """The dimensionality of inner projection layers in Transformer feed-forward
    networks."""

    dropout_p: float
    """The dropout probability in Transformer layers."""


nllb_archs = ArchitectureRegistry[NllbConfig]("nllb")

arch = nllb_archs.marker


@arch("dense_1b")
def nllb_dense_1b() -> NllbConfig:
    return NllbConfig(
        model_dim=1024,
        max_seq_len=1024,
        vocabulary_size=256206,
        pad_idx=0,
        num_encoder_layers=24,
        num_decoder_layers=24,
        num_encoder_attn_heads=16,
        num_decoder_attn_heads=16,
        ffn_inner_dim=1024 * 8,
        dropout_p=0.1,
    )


@arch("dense_3b")
def nllb_dense_3b() -> NllbConfig:
    return NllbConfig(
        model_dim=2048,
        max_seq_len=1024,
        vocabulary_size=256206,
        pad_idx=0,
        num_encoder_layers=24,
        num_decoder_layers=24,
        num_encoder_attn_heads=16,
        num_decoder_attn_heads=16,
        ffn_inner_dim=1024 * 8,
        dropout_p=0.1,
    )


@arch("dense_600m")
def nllb_dense_600m() -> NllbConfig:
    return NllbConfig(
        model_dim=1024,
        max_seq_len=1024,
        vocabulary_size=256206,
        pad_idx=0,
        num_encoder_layers=12,
        num_decoder_layers=12,
        num_encoder_attn_heads=16,
        num_decoder_attn_heads=16,
        ffn_inner_dim=1024 * 4,
        dropout_p=0.1,
    )


class NllbBuilder:
    """Builds modules of an NLLB model as described in
    :cite:t:`https://doi.org/10.48550/arxiv.2207.04672`.

    To tweak the architecture, you can derive from this class and override the
    corresponding methods.
    """

    config: NllbConfig
    device: Optional[Device]
    dtype: Optional[DataType]

    def __init__(
        self,
        config: NllbConfig,
        device: Optional[Device] = None,
        dtype: Optional[DataType] = None,
    ) -> None:
        """
        :param config:
            The configuration to use.
        :param device:
            The device on which to initialize modules.
        :param dtype:
            The data type of module parameters and buffers.
        """
        self.config = config
        self.device = device
        self.dtype = dtype

    def reset(self) -> None:
        """Reset the internal state of the builder."""

    def build_model(self) -> TransformerModel:
        """Build a model."""
        embed = Embedding(
            num_embeddings=self.config.vocabulary_size,
            embedding_dim=self.config.model_dim,
            pad_idx=self.config.pad_idx,
            scaled=True,
            device=self.device,
            dtype=self.dtype,
        )

        frontend = self.build_frontend(embed)

        encoder = self.build_encoder()
        decoder = self.build_decoder()

        final_proj = TiedProjection(embed.weight)

        return TransformerModel(
            frontend, encoder, frontend, decoder, final_proj, self.config.pad_idx
        )

    def build_frontend(self, embed: Embedding) -> TransformerFrontend:
        """Build a shared Transformer encoder/decoder front-end."""
        pos_encoder = SinusoidalPositionEncoder(
            self.config.model_dim,
            self.config.max_seq_len,
            _legacy_pad_idx=self.config.pad_idx,
            device=self.device,
            dtype=self.dtype,
        )

        return TransformerEmbeddingFrontend(
            embed,
            pos_encoder,
            dropout_p=self.config.dropout_p,
            device=self.device,
            dtype=self.dtype,
        )

    def build_encoder(self) -> TransformerEncoder:
        """Build a Transformer encoder."""
        num_layers = self.config.num_encoder_layers

        layers = [self.build_encoder_layer() for _ in range(num_layers)]

        return StandardTransformerEncoder(
            layers,
            norm_order=TransformerNormOrder.PRE,
            device=self.device,
            dtype=self.dtype,
        )

    def build_decoder(self) -> TransformerDecoder:
        """Build a Transformer decoder."""
        num_layers = self.config.num_decoder_layers

        layers = [self.build_decoder_layer() for _ in range(num_layers)]

        return StandardTransformerDecoder(
            layers,
            norm_order=TransformerNormOrder.PRE,
            device=self.device,
            dtype=self.dtype,
        )

    def build_encoder_layer(self) -> TransformerEncoderLayer:
        """Build a Transformer encoder layer."""
        self_attn = self.build_attention(self.config.num_encoder_attn_heads)

        ffn = self.build_ffn()

        return StandardTransformerEncoderLayer(
            self_attn,
            ffn,
            dropout_p=self.config.dropout_p,
            norm_order=TransformerNormOrder.PRE,
            device=self.device,
            dtype=self.dtype,
        )

    def build_decoder_layer(self) -> TransformerDecoderLayer:
        """Build a Transformer decoder layer."""
        self_attn = self.build_attention(self.config.num_decoder_attn_heads)

        encoder_decoder_attn = self.build_attention(self.config.num_decoder_attn_heads)

        ffn = self.build_ffn()

        return StandardTransformerDecoderLayer(
            self_attn,
            encoder_decoder_attn,
            ffn,
            dropout_p=self.config.dropout_p,
            norm_order=TransformerNormOrder.PRE,
            device=self.device,
            dtype=self.dtype,
        )

    def build_attention(self, num_heads: int) -> MultiheadAttention:
        """Build a Transformer multi-head attention layer."""
        sdpa = get_default_sdpa(attn_dropout_p=self.config.dropout_p)

        return StandardMultiheadAttention(
            self.config.model_dim,
            num_heads,
            sdpa=sdpa,
            device=self.device,
            dtype=self.dtype,
        )

    def build_ffn(self) -> FeedForwardNetwork:
        """Build a Transformer feed-forward network."""
        return StandardFeedForwardNetwork(
            self.config.model_dim,
            self.config.ffn_inner_dim,
            norm_order=TransformerNormOrder.PRE,
            device=self.device,
            dtype=self.dtype,
        )


def create_nllb_model(
    config: NllbConfig,
    device: Optional[Device] = None,
    dtype: Optional[DataType] = None,
) -> TransformerModel:
    """Create an NLLB model.

    :param config:
        The configuration to use.
    :param device:
        The device on which to initialize modules.
    :param dtype:
        The data type of module parameters and buffers.
    """
    return NllbBuilder(config, device, dtype).build_model()
