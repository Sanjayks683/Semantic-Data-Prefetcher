// ============================================================================
// sram_table.sv - Prediction table storage.
//
// Two decisions matter here:
//
//   1. Valid bits live in flip-flops and the payload lives in an unreset RAM.
//      Resetting the whole array in one always_ff block forces every bit into
//      a flop (TABLE_ENTRIES * ENTRY_WIDTH of them) and blocks BRAM/LUTRAM
//      inference. Splitting them keeps single-cycle invalidate while letting
//      the payload map to real memory.
//
//   2. Reads bypass an in-flight write to the same index. Without this, a
//      lookup that lands on the entry being updated in the same cycle sees the
//      pre-update value, and the design trains one access behind the
//      behavioural model.
// ============================================================================

`timescale 1ns / 1ps

import ngram_types_pkg::*;

module sram_table (
    input  logic                   clk,
    input  logic                   rst,
    input  logic [INDEX_BITS-1:0]  read_index,
    output table_entry_t           read_entry,
    input  logic                   write_en,
    input  logic [INDEX_BITS-1:0]  write_index,
    input  table_entry_t           write_entry
);

    logic [TABLE_ENTRIES-1:0] valid_bits;
    logic [PAYLOAD_WIDTH-1:0] payload_mem [TABLE_ENTRIES];

    wire [PAYLOAD_WIDTH-1:0] write_payload = {write_entry.tag,
                                             write_entry.pred_delta,
                                             write_entry.conf};

    // Read-during-write bypass to the same index.
    wire                     bypass       = write_en && (write_index == read_index);
    wire [PAYLOAD_WIDTH-1:0] read_payload = bypass ? write_payload : payload_mem[read_index];
    wire                     read_valid   = bypass ? write_entry.valid : valid_bits[read_index];

    assign read_entry = {read_valid, read_payload};

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            valid_bits <= '0;
        end else if (write_en) begin
            valid_bits[write_index] <= write_entry.valid;
        end
    end

    // No reset: this is the array that must infer as RAM.
    always_ff @(posedge clk) begin
        if (write_en) begin
            payload_mem[write_index] <= write_payload;
        end
    end

endmodule : sram_table
