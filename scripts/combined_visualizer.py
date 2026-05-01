import argparse
import os
import matplotlib.pyplot as plt
from matplotlib.widgets import Button

from Parsers.qiskitparser import QiskitParser
from qpdg import QPDG, get_rule_profile
from entanglement import EntanglementAnalyzer, EntanglementGraph

from visualizer import QPDGVisualizer
from entangle_viewer import visualize_entanglement_graph


class CombinedVisualizer:
    """
    Combined visualizer that creates a tabbed interface for viewing both the QPDG slice 
    and the Entanglement Graph.
    """

    def __init__(self, qpdg: QPDG, entangle_graph: EntanglementGraph, title: str = ""):
        self.qpdg = qpdg
        self.entangle_graph = entangle_graph

        # Create a single figure
        self.fig = plt.figure(figsize=(14, 8))
        self.fig.patch.set_facecolor("#1E1E2E")
        self.fig.canvas.manager.set_window_title(title)

        # Tab buttons at the top
        self.ax_tab_qpdg = self.fig.add_axes([0.3, 0.92, 0.18, 0.05])
        self.ax_tab_entangle = self.fig.add_axes([0.52, 0.92, 0.18, 0.05])

        self.btn_tab_qpdg = Button(
            self.ax_tab_qpdg, "QPDG Slice", color="#45475A", hovercolor="#585B70"
        )
        self.btn_tab_entangle = Button(
            self.ax_tab_entangle, "Entanglement Graph", color="#313244", hovercolor="#585B70"
        )
        
        self.btn_tab_qpdg.label.set_color("white")
        self.btn_tab_entangle.label.set_color("white")
        self.btn_tab_qpdg.label.set_fontsize(11)
        self.btn_tab_entangle.label.set_fontsize(11)

        self.btn_tab_qpdg.on_clicked(self._show_qpdg)
        self.btn_tab_entangle.on_clicked(self._show_entangle)

        # Main axes for the two views
        # QPDG slice leaves some room at the bottom for its navigation buttons
        self.ax_qpdg = self.fig.add_axes([0.05, 0.15, 0.9, 0.73])
        # Entanglement graph can use more space since it doesn't have bottom buttons
        self.ax_entangle = self.fig.add_axes([0.05, 0.05, 0.9, 0.83])

        # Initialize the two visualizers onto our axes
        self.qpdg_vis = QPDGVisualizer(
            self.qpdg, title="", fig=self.fig, ax=self.ax_qpdg, show=False
        )
        visualize_entanglement_graph(self.entangle_graph, show=False, ax=self.ax_entangle)

        # Start by showing the QPDG tab
        self._show_qpdg(None)

        plt.show()

    def _show_qpdg(self, event):
        # Hide entanglement view
        self.ax_entangle.set_visible(False)
        
        # Show QPDG view and its control buttons
        self.ax_qpdg.set_visible(True)
        self.qpdg_vis.ax_prev.set_visible(True)
        self.qpdg_vis.ax_next.set_visible(True)
        self.qpdg_vis.ax_toggle.set_visible(True)

        # Update tab active states (colors)
        self.btn_tab_qpdg.color = "#45475A"  # Active color
        self.ax_tab_qpdg.set_facecolor("#45475A")
        
        self.btn_tab_entangle.color = "#313244"  # Inactive color
        self.ax_tab_entangle.set_facecolor("#313244")

        self.fig.canvas.draw_idle()

    def _show_entangle(self, event):
        # Hide QPDG view and its control buttons
        self.ax_qpdg.set_visible(False)
        self.qpdg_vis.ax_prev.set_visible(False)
        self.qpdg_vis.ax_next.set_visible(False)
        self.qpdg_vis.ax_toggle.set_visible(False)

        # Show entanglement view
        self.ax_entangle.set_visible(True)

        # Update tab active states (colors)
        self.btn_tab_qpdg.color = "#313244"  # Inactive color
        self.ax_tab_qpdg.set_facecolor("#313244")
        
        self.btn_tab_entangle.color = "#45475A"  # Active color
        self.ax_tab_entangle.set_facecolor("#45475A")

        self.fig.canvas.draw_idle()


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Visualize both QPDG and Entanglement Graph in a combined tabbed interface.",
    )
    parser.add_argument(
        "--circuit_path",
        default=os.path.join("Attachments", "Sample Code", "grovers_algorithm_exception.py"),
        help="Path to the circuit .py file to parse.",
    )
    parser.add_argument(
        "--profile",
        default="baseline",
        help="Dependency profile to visualize for QPDG.",
    )
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()

    # Parse the circuit program
    parser = QiskitParser()
    program = parser.parse_file(args.circuit_path)

    if len(program) == 0:
        raise ValueError(f"No instructions parsed from {args.circuit_path}")

    # Build QPDG
    print("Building QPDG...")
    qpdg = QPDG(program, rules=get_rule_profile(args.profile))

    # Build Entanglement Graph
    print("Building Entanglement Graph...")
    analyzer = EntanglementAnalyzer(program)
    analyzer.analyze()
    graph = EntanglementGraph(analyzer)

    title = f"{os.path.basename(args.circuit_path)} ({args.profile})"
    
    # Launch the combined visualizer
    print("Launching combined visualizer...")
    CombinedVisualizer(qpdg, graph, title=title)