"""
EIDSF research engine — implementation of the constrained FMCG inventory model
of the article "Explainable Inventory Decision Support through Computational
Intelligence" (Section 4) and of the FMCG-ABC procedure (Section 5).

Modules
  model      portfolio data, objective TC(X), constraints, feasibility (eqs. 12-46)
  reference  Lagrangian lower bound and reference solution (certifies gaps)
  algorithms ABC variants A1-A4 (FMCG-ABC), GA, PSO, EOQ-with-static-SS baseline
  replay     daily policy replay with backorders (realised CSL, fill rate, costs)
  generator  documented synthetic portfolio generator (provenance: synthetic)
  artifact   SKU- and portfolio-level decision artifact, rules R1-R6
"""
VERSION = "eidsf/1.0"
