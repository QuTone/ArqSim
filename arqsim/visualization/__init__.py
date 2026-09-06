"""Pure views over ArqSim's canonical data contracts.

Visualization never participates in compilation or evaluation.  Each helper
accepts an immutable public object and returns a Matplotlib figure, so figures
cannot silently alter architectural state.
"""

from .architecture import (
    plot_architecture_hierarchy,
    plot_architecture_profile,
    plot_architecture_specification,
    plot_architecture_slot_layout,
    plot_module_cube_layout,
    plot_specification_overall_layout,
)
from .breakdown import plot_space_breakdown
from .circuit import plot_circuit_layers
from .dag import plot_program_dag, plot_resource_dag
from .timeline import plot_execution_timeline

__all__ = [
    "plot_circuit_layers",
    "plot_architecture_hierarchy",
    "plot_architecture_profile",
    "plot_architecture_specification",
    "plot_architecture_slot_layout",
    "plot_module_cube_layout",
    "plot_specification_overall_layout",
    "plot_execution_timeline",
    "plot_program_dag",
    "plot_resource_dag",
    "plot_space_breakdown",
]
