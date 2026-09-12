// ============================================================================
// xor_hash.sv - Folds the n-gram delta window into an (index, tag) pair.
//
// Each tap is rotated left by a distinct compile-time amount and XOR-folded.
// Purely combinational; mirrors NGramPrefetcher._compute_hash in the model.
// ============================================================================

`timescale 1ns / 1ps

import ngram_types_pkg::*;

module xor_hash (
    input  logic [NGRAM_DEPTH*DELTA_WIDTH-1:0] history_bus,
    output logic [INDEX_BITS-1:0]              table_index,
    output logic [TAG_BITS-1:0]                tag_out
);

    // The fold produces DELTA_WIDTH bits and is then split into an index field
    // and a tag field, so the two together must fit inside one delta word.
    if (INDEX_BITS + TAG_BITS > DELTA_WIDTH)
        $fatal(1, "xor_hash: INDEX_BITS + TAG_BITS exceeds DELTA_WIDTH");

    logic [DELTA_WIDTH-1:0] rotated [NGRAM_DEPTH];

    for (genvar g = 0; g < NGRAM_DEPTH; g++) begin : gen_taps
        localparam int R = ngram_types_pkg::ngram_rotate(g);
        wire [DELTA_WIDTH-1:0] tap = history_bus[g*DELTA_WIDTH +: DELTA_WIDTH];

        if (R == 0) begin : gen_no_rot
            assign rotated[g] = tap;
        end else begin : gen_rot
            assign rotated[g] = {tap[DELTA_WIDTH-1-R:0], tap[DELTA_WIDTH-1 -: R]};
        end
    end

    logic [DELTA_WIDTH-1:0] full_hash;

    always_comb begin
        full_hash = '0;
        for (int i = 0; i < NGRAM_DEPTH; i++) begin
            full_hash ^= rotated[i];
        end
    end

    assign table_index = full_hash[INDEX_BITS-1:0];
    assign tag_out     = full_hash[INDEX_BITS +: TAG_BITS];

endmodule : xor_hash
