package ngram_types_pkg;

    localparam int ADDR_WIDTH          = 64;
    localparam int BLOCK_OFFSET_BITS   = 6;
    localparam int BLOCK_ADDR_WIDTH    = ADDR_WIDTH - BLOCK_OFFSET_BITS;
    
    localparam int DELTA_WIDTH         = 16;
    localparam int NGRAM_DEPTH         = 3;
    
    localparam int TABLE_ENTRIES       = 1024;
    localparam int INDEX_BITS          = 10;
    localparam int TAG_BITS            = 6;
    localparam int CONF_BITS           = 2;
    localparam int CONF_THRESHOLD      = 2;

    typedef enum logic [1:0] {
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

endpackage : ngram_types_pkg
