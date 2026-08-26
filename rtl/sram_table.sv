
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

    always_ff @(posedge clk) begin
        if (write_en) begin
            payload_mem[write_index] <= write_payload;
        end
    end

endmodule : sram_table
