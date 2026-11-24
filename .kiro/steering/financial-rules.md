---
inclusion: always
---

# VeriCOBOL: Financial Engineering and Sandbox Rules

## 1. No floats for money
NEVER use IEEE 754 `float` for monetary amounts, balances, interest rates, fees, or any intermediate financial math. This includes `float()`, float literals, `math.pow`, `**` with float operands, and JSON number parsing into floats. Parse JSON numbers with `parse_float=Decimal` and accept amounts as strings where possible.

## 2. Decimal with COBOL PIC / ROUNDED semantics
- Use `decimal.Decimal` with explicit quantization, for example `Decimal("0.01")`.
- Match the scale of each COBOL `PIC` clause (for example `PIC 9(7)V99` -> scale 2).
- Rounding modes:
  - COBOL default (no `ROUNDED`) is truncation -> `ROUND_DOWN`.
  - COBOL `ROUNDED` -> `ROUND_HALF_UP`.
- Apply rounding at exactly the points COBOL stores into a receiving field. Intermediate COBOL results are not rounded until stored. Mirror that order.
- Use an explicit `decimal.Context` with sufficient precision. Never rely on the ambient context.
- Respect `SIZE ERROR`/overflow behavior: model the receiving field's digit capacity.

## 3. Compile-once batched differential PBT
- Compile each COBOL program ONCE per verification run into a temporary binary.
- Run all 150-200 Hypothesis cases against that single binary. NEVER recompile inside the test loop.
- Prefer batching many inputs per process invocation when the program design allows it. Total verification must finish in under 3 seconds on 0.1 CPU.
- Required edge cases: $0.00, maximum PIC values, negative and overdraft thresholds, repeating decimals (3.333%), half-cent boundaries (x.xx5), one-month terms, zero rate.
- Pass criterion: every output field equals the COBOL output exactly (difference of $0.00).
- On mismatch, report the exact counterexample: inputs, COBOL output, Python output, differing field, and likely cause (float drift, `ROUNDED` vs truncation, scale mismatch). Allow at most 2 self-healing retries.

## 4. COBOL sandbox security
Applies to all user-pasted COBOL, before compiling.
- Statically reject: `CALL "SYSTEM"` and any `CALL` to non-allow-listed programs, `OPEN OUTPUT`/`OPEN EXTEND`/`OPEN I-O`, file I/O (`SELECT ... ASSIGN`, `READ`, `WRITE`, `REWRITE`, `DELETE`), `ACCEPT ... FROM ENVIRONMENT`, `DISPLAY ... UPON ENVIRONMENT`, `COPY`/`REPLACE` pulling external files, and any shell access.
- Compile and run inside a fresh `tempfile.TemporaryDirectory`, always cleaned up (use try/finally or context manager).
- Subprocess timeout of 5 s per compile and per run. Use argument lists, never `shell=True`.
- Cap source size and output size. Treat all COBOL, stdout, and LLM output as untrusted data.
- Resolve the compiler from `COBC_PATH`.
