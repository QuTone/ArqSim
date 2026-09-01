OPENQASM 2.0;
include "qelib1.inc";

qreg q[3];
creg c[3];

// Prepare |T> = T H |0> on source qubit q[0].
h q[0];
t q[0];

// Prepare the Bell pair shared by q[1] and destination q[2].
h q[1];
cx q[1],q[2];

// Rotate source q[0] and Bell-half q[1] into the Bell measurement basis.
cx q[0],q[1];
h q[0];

// Defer measurement feed-forward as coherent X and Z corrections.
cx q[1],q[2];
cz q[0],q[2];

// Bell measurement and destination readout occur after coherent correction.
measure q[0] -> c[0];
measure q[1] -> c[1];
measure q[2] -> c[2];
