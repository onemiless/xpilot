"""Xpilot runtime rules failure matrix (G1).

R1: malformed ABI/type/length/count/reserved fields or >4096 bytes accepted.
R2: bit coverage overlap/gap, invalid field/range/tuple/checksum/counter accepted.
R3: duplicate TX address or mode-owned address (any bus/length) accepted.
R4: EP2 before BEGIN, wrong upload/offset/flags/size, truncated upload or bad CRC committed.
R5: car-mode BEGIN/COMMIT accepted; boot/non-car switch retains table; mode set retains runtime state.
R6: non-Tesla/no-vehicle/relay/noOutput/silent/controls gates bypassed; rejected TX spends budget.
R7: mode-owned TX dispatch falls through to rules; unknown or wrong bus/length TX accepted.
R8: auto_speed/ambient/navassist verdict or budget differs on replay/random streams.
R9: TTL/interval/idle/span/window +/-1 us or uint32 timer wrap differs.
R10: navassist cancel consumes or is blocked by session budget, or skips other gates.
R11: failed replacement leaves an active/partially loaded table; explicit empty table fails.
R12: old profile bits still grant production TX; production includes legacy headers.
"""
