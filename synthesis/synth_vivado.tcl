# ==============================================================================
# synth_vivado.tcl - Synthesis, place and route for the N-Gram prefetcher.
#
# Run from anywhere:
#     vivado -mode batch -source synthesis/synth_vivado.tcl
#
# Optional overrides:
#     vivado -mode batch -source synthesis/synth_vivado.tcl -tclargs \
#            -part xcku040-ffva1156-2-e -period 3.0 -no_route
#
# All paths are resolved relative to this script, and reports are written next
# to it, so the working directory does not matter.
# ==============================================================================

set script_dir [file normalize [file dirname [info script]]]
set repo_dir   [file dirname $script_dir]
set rtl_dir    [file join $repo_dir rtl]
set report_dir [file join $script_dir reports]

# ------------------------------------------------------------------ options --
set part        "xc7a100tcsg324-1"
set clk_period  4.000
set run_route   1

for {set i 0} {$i < [llength $argv]} {incr i} {
    switch -- [lindex $argv $i] {
        -part     { incr i; set part       [lindex $argv $i] }
        -period   { incr i; set clk_period [lindex $argv $i] }
        -no_route { set run_route 0 }
        default   { puts "warning: ignoring unknown argument [lindex $argv $i]" }
    }
}

file mkdir $report_dir

puts "\[SYNTH\] part        : $part"
puts "\[SYNTH\] clk period  : $clk_period ns ([format %.1f [expr {1000.0 / $clk_period}]] MHz)"
puts "\[SYNTH\] reports     : $report_dir"

# ------------------------------------------------------------------ project --
create_project -in_memory -part $part

# The package must be read first; everything else depends on it.
set sources {
    ngram_types_pkg.sv
    delta_generator.sv
    history_shift_reg.sv
    xor_hash.sv
    sram_table.sv
    confidence_fsm.sv
    ngram_prefetcher.sv
}

foreach f $sources {
    set path [file join $rtl_dir $f]
    if {![file exists $path]} {
        error "missing RTL source: $path"
    }
    read_verilog -sv $path
}

# ------------------------------------------------------------- constraints --
# Read before synthesis so the tool optimises against the real timing goal.
set xdc [file join $script_dir constraints.xdc]
if {![file exists $xdc]} {
    error "missing constraints file: $xdc"
}
read_xdc $xdc

# Apply the period override, if any, on top of what the XDC declares.
set_property -dict [list \
    STEPS.SYNTH_DESIGN.ARGS.FLATTEN_HIERARCHY rebuilt \
] [current_project]

# ------------------------------------------------------------------ synth ---
synth_design -top ngram_prefetcher -part $part -mode out_of_context
if {$clk_period != 4.000} {
    create_clock -period $clk_period -name clk [get_ports clk]
}
write_checkpoint -force [file join $report_dir post_synth.dcp]
report_utilization -file [file join $report_dir post_synth_utilization.txt]
report_timing_summary -file [file join $report_dir post_synth_timing.txt]

# ------------------------------------------------- implementation (opt/P&R) --
if {$run_route} {
    opt_design
    place_design
    phys_opt_design
    route_design

    write_checkpoint -force [file join $report_dir post_route.dcp]
    report_utilization    -file [file join $report_dir post_route_utilization.txt]
    report_timing_summary -file [file join $report_dir post_route_timing.txt]
    report_power          -file [file join $report_dir post_route_power.txt]

    # Post-route slack is the only number worth quoting as an achieved Fmax.
    set wns [get_property SLACK [get_timing_paths -delay_type max]]
    set achieved [expr {1000.0 / ($clk_period - $wns)}]

    puts "\[SYNTH\] ------------------------------------------------------"
    puts "\[SYNTH\] post-route WNS : $wns ns"
    puts "\[SYNTH\] achieved Fmax  : [format %.1f $achieved] MHz"
    puts "\[SYNTH\] LUTs           : [llength [get_cells -hier -filter {PRIMITIVE_GROUP == LUT}]]"
    puts "\[SYNTH\] Registers      : [llength [get_cells -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH}]]"
    puts "\[SYNTH\] Block RAM      : [llength [get_cells -hier -filter {PRIMITIVE_GROUP == BLOCKRAM}]]"
    puts "\[SYNTH\] ------------------------------------------------------"

    if {$wns < 0} {
        puts "\[SYNTH\] TIMING NOT MET at $clk_period ns - see post_route_timing.txt"
    } else {
        puts "\[SYNTH\] Timing met at $clk_period ns."
    }
} else {
    puts "\[SYNTH\] -no_route given: stopping after synthesis."
    puts "\[SYNTH\] Post-synthesis timing is an estimate; do not quote it as Fmax."
}

puts "\[SYNTH\] Done. Reports in $report_dir"
