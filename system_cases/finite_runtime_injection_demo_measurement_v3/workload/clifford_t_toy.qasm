OPENQASM 2.0;
include "qelib1.inc";

qreg q[4];

// Wave A consumes one remote T-magic state and exercises correction outcome 1.
h q[0];
h q[1];
barrier q;
t q[0];
barrier q;
cx q[0],q[1];
barrier q;

// Wave B swaps the active profile-2.3 compute residency and exercises outcome 0.
h q[2];
h q[3];
barrier q;
t q[2];
barrier q;
cx q[2],q[3];
