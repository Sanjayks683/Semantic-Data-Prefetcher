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

    localparam int ENTRY_WIDTH = $bits(table_entry_t);
    logic [ENTRY_WIDTH-1:0] mem [TABLE_ENTRIES];
    integer i;

    assign read_entry = mem[read_index];

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            for (i = 0; i < TABLE_ENTRIES; i = i + 1) begin
                mem[i] <= '0;
            end
        end else if (write_en) begin
            mem[write_index] <= write_entry;
        end
    end

endmodule : sram_table
