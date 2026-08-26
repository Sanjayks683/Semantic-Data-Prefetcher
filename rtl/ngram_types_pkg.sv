
package ngram_types_pkg;

    localparam int ADDR_WIDTH        = 64;
    localparam int BLOCK_SIZE_BYTES  = 64;
    localparam int BLOCK_OFFSET_BITS = $clog2(BLOCK_SIZE_BYTES);
    localparam int BLOCK_ADDR_WIDTH  = ADDR_WIDTH - BLOCK_OFFSET_BITS;

    localparam int DELTA_WIDTH   = 16;

`ifdef NGRAM_V2
    localparam int NGRAM_DEPTH   = 2;
`else
    localparam int NGRAM_DEPTH   = 3;
`endif

    localparam int TABLE_ENTRIES = 1024;
    localparam int INDEX_BITS    = $clog2(TABLE_ENTRIES);
    localparam int TAG_BITS      = 6;

    localparam int CONF_BITS      = 2;
    localparam int CONF_MAX       = (1 << CONF_BITS) - 1;
    localparam int CONF_THRESHOLD = 2;

    localparam longint DELTA_MIN = -(64'sd1 <<< (DELTA_WIDTH - 1));
    localparam longint DELTA_MAX =  (64'sd1 <<< (DELTA_WIDTH - 1)) - 1;

    typedef enum logic [CONF_BITS-1:0] {
        CONF_STRONGLY_UNLIKELY = 2'b00,
        CONF_WEAKLY_UNLIKELY   = 2'b01,
        CONF_WEAKLY_LIKELY     = 2'b10,
        CONF_STRONGLY_LIKELY   = 2'b11
    } conf_state_e;

    typedef struct packed {
        logic                          valid;
        logic [TAG_BITS-1:0]           tag;
        logic signed [DELTA_WIDTH-1:0] pred_delta;
        logic [CONF_BITS-1:0]          conf;
    } table_entry_t;

    localparam int ENTRY_WIDTH   = $bits(table_entry_t);
    localparam int PAYLOAD_WIDTH = TAG_BITS + DELTA_WIDTH + CONF_BITS;

    function automatic int ngram_rotate(input int i);
        return (i == 0) ? 0 : ((i * 4 - 1) % DELTA_WIDTH);
    endfunction

endpackage : ngram_types_pkg
