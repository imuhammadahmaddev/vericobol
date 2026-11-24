*> Compound Interest Engine (Batch_Protocol)
*> Input row : principal annual_rate_pct compounds_per_year years
*> Output row: maturity_amount accrued_interest
*> Running balance is stored ROUNDED (half-up) every period.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. COMPOUND-INT.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-LINE             PIC X(200).
       01 WS-TOK-1            PIC X(40).
       01 WS-TOK-2            PIC X(40).
       01 WS-TOK-3            PIC X(40).
       01 WS-TOK-4            PIC X(40).
       01 WS-EOF              PIC 9 VALUE 0.
       01 WS-PRINCIPAL        PIC 9(9)V99.
       01 WS-RATE-PCT         PIC 9(3)V999.
       01 WS-PER-YEAR         PIC 9(2).
       01 WS-YEARS            PIC 9(2).
       01 WS-PERIODS          PIC 9(4).
       01 WS-PERIOD-IDX       PIC 9(4).
       01 WS-DENOM            PIC 9(6)V999.
       01 WS-NUMER            PIC 9(6)V999.
       01 WS-BALANCE          PIC 9(9)V99.
       01 WS-MATURITY         PIC 9(9)V99.
       01 WS-ACCRUED          PIC S9(9)V99.
       01 WS-ED-MATURITY      PIC Z(8)9.99.
       01 WS-ED-ACCRUED       PIC -(8)9.99.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM UNTIL WS-EOF = 1
               MOVE HIGH-VALUES TO WS-LINE
               ACCEPT WS-LINE FROM SYSIN
               IF WS-LINE = SPACES AND
                  FUNCTION EXCEPTION-STATUS = "EC-IMP-ACCEPT"
                   MOVE 1 TO WS-EOF
               ELSE
                   PERFORM PROCESS-ROW
               END-IF
           END-PERFORM
           STOP RUN.

       PROCESS-ROW.
           IF WS-LINE = SPACES
               EXIT PARAGRAPH
           END-IF
           MOVE SPACES TO WS-TOK-1 WS-TOK-2 WS-TOK-3 WS-TOK-4
           UNSTRING FUNCTION TRIM(WS-LINE) DELIMITED BY ALL SPACE
               INTO WS-TOK-1 WS-TOK-2 WS-TOK-3 WS-TOK-4
           END-UNSTRING
           COMPUTE WS-PRINCIPAL = FUNCTION NUMVAL(WS-TOK-1)
           COMPUTE WS-RATE-PCT  = FUNCTION NUMVAL(WS-TOK-2)
           COMPUTE WS-PER-YEAR  = FUNCTION NUMVAL(WS-TOK-3)
           COMPUTE WS-YEARS     = FUNCTION NUMVAL(WS-TOK-4)
           COMPUTE WS-PERIODS = WS-PER-YEAR * WS-YEARS
           MOVE WS-PRINCIPAL TO WS-BALANCE
           IF WS-PERIODS > 0
*>        Factor (100 * n + rate) / (100 * n): one exact multiply,
*>        one division, rounded once when stored into the balance.
               COMPUTE WS-DENOM = 100 * WS-PER-YEAR
               COMPUTE WS-NUMER = WS-DENOM + WS-RATE-PCT
               PERFORM VARYING WS-PERIOD-IDX FROM 1 BY 1
                       UNTIL WS-PERIOD-IDX > WS-PERIODS
                   COMPUTE WS-BALANCE ROUNDED =
                       WS-BALANCE * WS-NUMER / WS-DENOM
               END-PERFORM
           END-IF
           MOVE WS-BALANCE TO WS-MATURITY
           COMPUTE WS-ACCRUED ROUNDED = WS-MATURITY - WS-PRINCIPAL
           MOVE WS-MATURITY TO WS-ED-MATURITY
           MOVE WS-ACCRUED TO WS-ED-ACCRUED
           DISPLAY FUNCTION TRIM(WS-ED-MATURITY) " "
                   FUNCTION TRIM(WS-ED-ACCRUED).
