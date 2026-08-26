
`timescale 1ns / 1ps

import ngram_types_pkg::*;

module confidence_fsm (
    input  table_entry_t                   current_entry,
    input  logic signed [DELTA_WIDTH-1:0]  actual_delta,
    input  logic [TAG_BITS-1:0]            expected_tag,
    output table_entry_t                   updated_entry
);

    wire                          entry_valid = current_entry.valid;
    wire [TAG_BITS-1:0]           entry_tag   = current_entry.tag;
    wire signed [DELTA_WIDTH-1:0] entry_delta = current_entry.pred_delta;
    wire [CONF_BITS-1:0]          entry_conf  = current_entry.conf;

    wire tag_hit    = entry_valid && (entry_tag == expected_tag);
    wire predicted  = (entry_delta == actual_delta);

    logic                          next_valid;
    logic [TAG_BITS-1:0]           next_tag;
    logic signed [DELTA_WIDTH-1:0] next_delta;
    logic [CONF_BITS-1:0]          next_conf;

    always_comb begin
        next_valid = 1'b1;
        next_tag   = expected_tag;
        next_delta = actual_delta;
        next_conf  = CONF_WEAKLY_UNLIKELY;

        if (tag_hit) begin
            if (predicted) begin
                next_delta = entry_delta;
                next_conf  = (entry_conf < CONF_MAX[CONF_BITS-1:0])
                           ? (entry_conf + 1'b1)
                           : CONF_MAX[CONF_BITS-1:0];
            end else if (entry_conf > CONF_WEAKLY_UNLIKELY) begin

                next_tag   = entry_tag;
                next_delta = entry_delta;
                next_conf  = entry_conf - 1'b1;
            end

        end
    end

    assign updated_entry.valid      = next_valid;
    assign updated_entry.tag        = next_tag;
    assign updated_entry.pred_delta = next_delta;
    assign updated_entry.conf       = next_conf;

endmodule : confidence_fsm
