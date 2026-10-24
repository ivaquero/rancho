"""Greenhouse economics as an agent-based layer on top of the crop physics.

The agricultural model in :mod:`rancho` answers *how much grows*.  It says
nothing about who gets the shared chiller, the well, the grid connection or the
fertiliser tank when three crops want them at the same hour.  This subpackage
adds that second layer with Mesa: one greenhouse agent owns the capacities,
one crop agent per bed bids for them, and the clearing order decides the cost.

The probe is deliberately small — three crops, two forcing profiles, one
season window — because its job is to test the coupling, not to forecast.
:mod:`rancho.econ.probe` runs the controlled comparison that decides whether
the mechanism is sound enough to hand over to CropForge.
"""

from __future__ import annotations

from rancho.econ.probe import ProbeResult, probe
from rancho.econ.spec import CROPS, GREENHOUSE, SITES, TARIFFS

__all__ = ["CROPS", "GREENHOUSE", "SITES", "TARIFFS", "ProbeResult", "probe"]
