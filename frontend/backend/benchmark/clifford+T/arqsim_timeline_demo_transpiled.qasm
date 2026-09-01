OPENQASM 2.0;
include "qelib1.inc";

qreg q[4];

// Wave A occupies the compute region and consumes one remote magic state.
h q[0];
h q[1];
barrier q;
t q[0];
barrier q;
cx q[0],q[1];
barrier q;

// Wave B makes profile 2.3 exchange one resident qubit with qLDPC memory.
h q[2];
h q[3];
barrier q;
t q[2];
barrier q;
cx q[2],q[3];
