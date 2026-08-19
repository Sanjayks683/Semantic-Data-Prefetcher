`timescale 1ns / 1ps

import ngram_types_pkg::*;

module xor_hash (
    input  logic [NGRAM_DEPTH*DELTA_WIDTH-1:0] history_bus,
    output logic [INDEX_BITS-1:0]              table_index,
    output logic [TAG_BITS-1:0]                tag_out
);

    wire [DELTA_WIDTH-1:0] tap0 = history_bus[0*DELTA_WIDTH +: DELTA_WIDTH];
    wire [DELTA_WIDTH-1:0] tap1 = history_bus[1*DELTA_WIDTH +: DELTA_WIDTH];
    wire [DELTA_WIDTH-1:0] tap2 = history_bus[2*DELTA_WIDTH +: DELTA_WIDTH];

    wire [DELTA_WIDTH-1:0] h0 = tap0;
    wire [DELTA_WIDTH-1:0] h1 = {tap1[DELTA_WIDTH-4:0], tap1[DELTA_WIDTH-1:DELTA_WIDTH-3]};
    wire [DELTA_WIDTH-1:0] h2 = {tap2[DELTA_WIDTH-8:0], tap2[DELTA_WIDTH-1:DELTA_WIDTH-7]};

    wire [DELTA_WIDTH-1:0] full_hash = h0 ^ h1 ^ h2;

    assign table_index = full_hash[INDEX_BITS-1:0];
    assign tag_out     = full_hash[INDEX_BITS+TAG_BITS-1:INDEX_BITS];

endmodule : xor_hash
