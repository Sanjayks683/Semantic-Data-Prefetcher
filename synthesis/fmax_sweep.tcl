# ==============================================================================
# fmax_sweep.tcl - Find the achievable clock frequency by implementing the
# design at several target periods and recording post-route slack.
#
#     vivado -mode batch -source synthesis/fmax_sweep.tcl
#     vivado -mode batch -source synthesis/fmax_sweep.tcl -tclargs -profile v2 -periods "4 14 15"
#
# -profile v2 builds the 2-delta variant (-verilog_define NGRAM_V2) and writes
# its checkpoint under reports/v2/, leaving the v1 results untouched.
#
# Extrapolating Fmax from a single badly-failing run is unreliable: the tool
# gives up differently when the target is far out of reach, and routing changes
# with the constraint. So each period gets its own place-and-route pass from a
# shared post-synthesis checkpoint.
# ==============================================================================

set script_dir [file normalize [file dirname [info script]]]
set repo_dir   [file dirname $script_dir]
set rtl_dir    [file join $repo_dir rtl]

set part    "xc7z020clg400-1"
set periods {4.0 8.0 12.0 14.0 15.0 16.0}
set profile "v1"

if {![info exists argv]} { set argv {} }
for {set i 0} {$i < [llength $argv]} {incr i} {
    switch -- [lindex $argv $i] {
        -profile { incr i; set profile [lindex $argv $i] }
        -periods { incr i; set periods [lindex $argv $i] }
        default  { puts "warning: ignoring unknown argument [lindex $argv $i]" }
    }
}

switch -- $profile {
    v1 {
        set define_args {}
        set report_dir  [file join $script_dir reports]
    }
    v2 {
        set define_args [list -verilog_define NGRAM_V2]
        set report_dir  [file join $script_dir reports v2]
    }
    default { error "unknown -profile '$profile' (expected v1 or v2)" }
}

file mkdir $report_dir
puts "\[SWEEP\] profile $profile, periods $periods"

set sources {
    ngram_types_pkg.sv delta_generator.sv history_shift_reg.sv
    xor_hash.sv sram_table.sv confidence_fsm.sv ngram_prefetcher.sv
}

create_project -in_memory -part $part
foreach f $sources {
    read_verilog -sv [list [file join $rtl_dir $f]]
}
read_xdc [list [file join $script_dir constraints.xdc]]

synth_design -top ngram_prefetcher -part $part -mode out_of_context {*}$define_args
set synth_dcp [file join $report_dir sweep_post_synth.dcp]
write_checkpoint -force $synth_dcp

set results {}

foreach p $periods {
    close_design
    open_checkpoint $synth_dcp

    # Replace the clock definition with this iteration's target, keeping the
    # same I/O budget the XDC declares.
    create_clock -period $p -name clk [get_ports clk]
    set_input_delay  -clock clk 1.000 [get_ports {rst mem_valid mem_addr_in[*]}]
    set_output_delay -clock clk 1.000 [get_ports {prefetch_valid prefetch_addr_out[*] delta_overflow}]
    set_false_path -from [get_ports rst]

    opt_design -quiet
    place_design -quiet
    phys_opt_design -quiet
    route_design -quiet

    set wns [get_property SLACK [get_timing_paths -delay_type max]]
    set met [expr {$wns >= 0 ? "MET" : "VIOLATED"}]
    set fmax [expr {1000.0 / ($p - $wns)}]

    lappend results [list $p $wns $met $fmax]
    puts "\[SWEEP\] period=$p ns  WNS=$wns ns  $met  implied_fmax=[format %.1f $fmax] MHz"
}

puts "\[SWEEP\] =============================================================="
puts "\[SWEEP\]  period(ns)   target(MHz)      WNS(ns)   status     Fmax(MHz)"
puts "\[SWEEP\] --------------------------------------------------------------"
foreach r $results {
    lassign $r p wns met fmax
    puts [format "\[SWEEP\]  %8.2f   %10.1f   %10.3f   %-9s  %8.1f" \
          $p [expr {1000.0 / $p}] $wns $met $fmax]
}
puts "\[SWEEP\] =============================================================="
