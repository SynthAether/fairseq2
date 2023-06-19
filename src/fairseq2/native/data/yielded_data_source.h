// Copyright (c) Meta Platforms, Inc. and affiliates.
// All rights reserved.
//
// This source code is licensed under the BSD-style license found in the
// LICENSE file in the root directory of this source tree.

#pragma once

#include <memory>
#include <utility>

#include "fairseq2/native/py.h"
#include "fairseq2/native/data/data_pipeline.h"
#include "fairseq2/native/data/data_source.h"

namespace fairseq2::detail {

class yielded_data_source final : public data_source {
public:
    explicit
    yielded_data_source(std::unique_ptr<data_source> &&inner, yield_fn &&fn) noexcept
        : inner_{std::move(inner)}, fn_{std::move(fn)}
    {}

    std::optional<data>
    next() override;

    void
    reset() override;

    void
    record_position(tape &t) const override;

    void
    reload_position(tape &t) override;

private:
    bool
    load_next_data_pipeline();

    data_pipeline
    invoke_yield_fn(data &example);

private:
    std::unique_ptr<data_source> inner_;
    yield_fn fn_;
    std::optional<data> example_{};
    data_pipeline data_pipeline_{};
};

}  // namespace fairseq2::detail
