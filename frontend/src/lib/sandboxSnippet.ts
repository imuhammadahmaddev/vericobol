/** Sample COBOL that the backend sandbox must reject (contains CALL "SYSTEM"). */
export const SANDBOX_TEST_SNIPPET = `       IDENTIFICATION DIVISION.
       PROGRAM-ID. SANDBOX-TEST.
       PROCEDURE DIVISION.
           CALL "SYSTEM" USING "echo pwned".
           STOP RUN.
`;
