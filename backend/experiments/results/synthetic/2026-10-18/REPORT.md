# Computational study — auto-generated report (SYNTHETIC DATA)

> **Provenance: synthetic.** These results come from `sample_inventory.csv` and do NOT
> describe the MPhil research dataset or any real distributor.

Generated 2026-10-18T15:17:11.303588+00:00 in 282.9 s. Data: `sample_inventory.csv`
(64 SKUs). Seeds 1..30; 10000 objective evaluations per run;
target cycle service level 0.95; h = 0.2; S = 50.0.
Budget tightness t means the budget is t x the average working capital the unconstrained
EOQ policy needs (t = 1.0: budget not binding).

> Interpretation guard: the planning model is convex, so the exact Lagrangian/KKT solution
> is the true optimum. Gaps below are measured against it. Results on synthetic data
> describe algorithm behaviour, not real-world savings.

## RQ-A Solution quality (gap to exact optimum, %)
| budget_tightness | init | algorithm | mean_gap_pct | sd_gap_pct | median_gap_pct | worst_gap_pct | success_rate_gap_lt_0.1pct | mean_runtime_sec |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.4000 | eoq_seeded | ABC | 0.3594 | 0.0151 | 0.3638 | 0.3746 | 0.0000 | 0.8262 |
| 0.4000 | eoq_seeded | GA | 0.1934 | 0.0995 | 0.1482 | 0.3686 | 0.1333 | 0.3287 |
| 0.4000 | eoq_seeded | PSO | 0.1210 | 0.0369 | 0.1151 | 0.1904 | 0.2667 | 0.0419 |
| 0.4000 | random | ABC | 0.7899 | 0.2174 | 0.7858 | 1.2641 | 0.0000 | 0.8870 |
| 0.4000 | random | GA | 1.1899 | 0.3584 | 1.1295 | 2.2324 | 0.0000 | 0.3626 |
| 0.4000 | random | PSO | 0.1111 | 0.1580 | 0.0368 | 0.5704 | 0.7333 | 0.0401 |
| 0.6000 | eoq_seeded | ABC | 0.0046 | 0.0001 | 0.0046 | 0.0047 | 1.0000 | 0.8893 |
| 0.6000 | eoq_seeded | GA | 0.0015 | 0.0004 | 0.0015 | 0.0025 | 1.0000 | 0.3165 |
| 0.6000 | eoq_seeded | PSO | 0.0002 | 0.0001 | 0.0002 | 0.0004 | 1.0000 | 0.0402 |
| 0.6000 | random | ABC | 0.9588 | 0.2270 | 0.9656 | 1.5351 | 0.0000 | 0.8590 |
| 0.6000 | random | GA | 1.0991 | 0.3066 | 1.0499 | 1.8920 | 0.0000 | 0.3617 |
| 0.6000 | random | PSO | 0.1157 | 0.1572 | 0.0313 | 0.4601 | 0.7000 | 0.0385 |
| 0.8000 | eoq_seeded | ABC | 0.0002 | 0.0000 | 0.0003 | 0.0003 | 1.0000 | 0.8386 |
| 0.8000 | eoq_seeded | GA | 0.0001 | 0.0000 | 0.0001 | 0.0001 | 1.0000 | 0.3551 |
| 0.8000 | eoq_seeded | PSO | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0402 |
| 0.8000 | random | ABC | 0.8073 | 0.2133 | 0.7830 | 1.1797 | 0.0000 | 0.9436 |
| 0.8000 | random | GA | 0.7989 | 0.2191 | 0.7760 | 1.5816 | 0.0000 | 0.3749 |
| 0.8000 | random | PSO | 0.0883 | 0.1017 | 0.0387 | 0.3278 | 0.6667 | 0.0360 |

## Exact optimum and proportional-scaling heuristic
| study | n_skus | budget_tightness | init | exact_cost | lambda_budget | proportional_scaling_gap_pct |
| --- | --- | --- | --- | --- | --- | --- |
| quality | 64 | 0.8000 | random | 251726.9646 | 0.2203 | 0.0003 |
| quality | 64 | 0.8000 | eoq_seeded | 251726.9646 | 0.2203 | 0.0003 |
| quality | 64 | 0.6000 | random | 333506.0825 | 1.1873 | 0.0047 |
| quality | 64 | 0.6000 | eoq_seeded | 333506.0825 | 1.1873 | 0.0047 |
| quality | 64 | 0.4000 | random | 1403754.7085 | 41.1668 | 0.3746 |
| quality | 64 | 0.4000 | eoq_seeded | 1403754.7085 | 41.1668 | 0.3746 |
| scalability | 64 | 0.6000 | random | 333506.0825 |  | 0.0047 |
| scalability | 256 | 0.6000 | random | 1374659.0249 |  | 0.0062 |
| scalability | 1024 | 0.6000 | random | 5298970.0731 |  | 0.0050 |

## Statistical tests (gap %, blocks = seeds; Holm-corrected pairwise p-values)
| budget_tightness | init | test | comparison | statistic | p_value | p_holm | effect_size_rank_biserial |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.4000 | eoq_seeded | Friedman | ABC vs GA vs PSO | 43.4000 | 0.0000 |  |  |
| 0.4000 | eoq_seeded | Wilcoxon signed-rank | ABC vs GA | 8.0000 | 0.0000 | 0.0000 | 0.9656 |
| 0.4000 | eoq_seeded | Wilcoxon signed-rank | ABC vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.4000 | eoq_seeded | Wilcoxon signed-rank | GA vs PSO | 131.0000 | 0.0364 | 0.0364 | 0.4366 |
| 0.4000 | random | Friedman | ABC vs GA vs PSO | 51.6667 | 0.0000 |  |  |
| 0.4000 | random | Wilcoxon signed-rank | ABC vs GA | 35.0000 | 0.0000 | 0.0000 | -0.8495 |
| 0.4000 | random | Wilcoxon signed-rank | ABC vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.4000 | random | Wilcoxon signed-rank | GA vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.6000 | eoq_seeded | Friedman | ABC vs GA vs PSO | 60.0000 | 0.0000 |  |  |
| 0.6000 | eoq_seeded | Wilcoxon signed-rank | ABC vs GA | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.6000 | eoq_seeded | Wilcoxon signed-rank | ABC vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.6000 | eoq_seeded | Wilcoxon signed-rank | GA vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.6000 | random | Friedman | ABC vs GA vs PSO | 45.0667 | 0.0000 |  |  |
| 0.6000 | random | Wilcoxon signed-rank | ABC vs GA | 155.0000 | 0.1142 | 0.1142 | -0.3333 |
| 0.6000 | random | Wilcoxon signed-rank | ABC vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.6000 | random | Wilcoxon signed-rank | GA vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.8000 | eoq_seeded | Friedman | ABC vs GA vs PSO | 60.0000 | 0.0000 |  |  |
| 0.8000 | eoq_seeded | Wilcoxon signed-rank | ABC vs GA | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.8000 | eoq_seeded | Wilcoxon signed-rank | ABC vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.8000 | eoq_seeded | Wilcoxon signed-rank | GA vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.8000 | random | Friedman | ABC vs GA vs PSO | 45.0667 | 0.0000 |  |  |
| 0.8000 | random | Wilcoxon signed-rank | ABC vs GA | 212.0000 | 0.6850 | 0.6850 | 0.0882 |
| 0.8000 | random | Wilcoxon signed-rank | ABC vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 0.8000 | random | Wilcoxon signed-rank | GA vs PSO | 0.0000 | 0.0000 | 0.0000 | 1.0000 |

## RQ-B Scalability (budget tightness 0.6, random initialisation)
| n_skus | algorithm | mean_gap_pct | sd_gap_pct | worst_gap_pct | mean_runtime_sec |
| --- | --- | --- | --- | --- | --- |
| 64 | ABC | 0.9353 | 0.1828 | 1.1823 | 0.8961 |
| 64 | GA | 1.1479 | 0.4102 | 1.8920 | 0.3354 |
| 64 | PSO | 0.2152 | 0.1888 | 0.4601 | 0.0407 |
| 256 | ABC | 20.1499 | 2.7895 | 23.2160 | 0.8841 |
| 256 | GA | 16.8360 | 1.6369 | 18.6835 | 0.3973 |
| 256 | PSO | 2.0450 | 0.3591 | 2.5314 | 0.0882 |
| 1024 | ABC | 58.3354 | 3.8140 | 65.2904 | 1.1302 |
| 1024 | GA | 45.6944 | 1.7270 | 47.7379 | 0.8096 |
| 1024 | PSO | 7.5707 | 0.7523 | 8.7240 | 0.3312 |

## RQ-C Simulated operational performance (30 replications, common random numbers)
| budget_tightness | policy | total_cost_mean | total_cost_ci95_low | total_cost_ci95_high | fill_rate_pct | cycle_service_level_pct | avg_inventory_value | planned_budget_use | budget_limit | saving_vs_current_mean | saving_vs_current_ci95_low | saving_vs_current_ci95_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.00 | Current policy | 1,897,023.89 | 1,874,813.38 | 1,919,234.41 | 96.73 | 50.40 | 1,243,218.15 |  | 725,539.22 | 0.00 | 0.00 | 0.00 |
| 1.00 | Classical EOQ/SS | 783,775.31 | 765,911.40 | 801,639.22 | 99.17 | 86.89 | 644,144.17 | 725,539.22 | 725,539.22 | 1,113,248.58 | 1,084,447.81 | 1,142,049.35 |
| 1.00 | Constrained optimum | 783,775.31 | 765,911.40 | 801,639.22 | 99.17 | 86.89 | 644,144.17 | 725,539.22 | 725,539.22 | 1,113,248.58 | 1,084,447.81 | 1,142,049.35 |
| 0.80 | Current policy | 1,897,023.89 | 1,874,813.38 | 1,919,234.41 | 96.73 | 50.40 | 1,243,218.15 |  | 580,431.38 | 0.00 | 0.00 | 0.00 |
| 0.80 | Classical EOQ/SS | 783,775.31 | 765,911.40 | 801,639.22 | 99.17 | 86.89 | 644,144.17 | 725,539.22 | 580,431.38 | 1,113,248.58 | 1,084,447.81 | 1,142,049.35 |
| 0.80 | Constrained optimum | 931,518.52 | 908,134.90 | 954,902.13 | 98.95 | 87.60 | 501,813.58 | 580,431.38 | 580,431.38 | 965,505.37 | 932,797.08 | 998,213.67 |
| 0.60 | Current policy | 1,897,023.89 | 1,874,813.38 | 1,919,234.41 | 96.73 | 50.40 | 1,243,218.15 |  | 435,323.53 | 0.00 | 0.00 | 0.00 |
| 0.60 | Classical EOQ/SS | 783,775.31 | 765,911.40 | 801,639.22 | 99.17 | 86.89 | 644,144.17 | 725,539.22 | 435,323.53 | 1,113,248.58 | 1,084,447.81 | 1,142,049.35 |
| 0.60 | Constrained optimum | 1,268,072.42 | 1,242,384.98 | 1,293,759.85 | 98.51 | 88.70 | 357,708.59 | 435,323.53 | 435,323.53 | 628,951.47 | 600,286.98 | 657,615.96 |
| 0.40 | Current policy | 1,897,023.89 | 1,874,813.38 | 1,919,234.41 | 96.73 | 50.40 | 1,243,218.15 |  | 290,215.69 | 0.00 | 0.00 | 0.00 |
| 0.40 | Classical EOQ/SS | 783,775.31 | 765,911.40 | 801,639.22 | 99.17 | 86.89 | 644,144.17 | 725,539.22 | 290,215.69 | 1,113,248.58 | 1,084,447.81 | 1,142,049.35 |
| 0.40 | Constrained optimum | 2,618,816.01 | 2,596,506.24 | 2,641,125.79 | 97.36 | 90.62 | 218,336.07 | 290,215.69 | 290,215.69 | -721,792.12 | -751,460.85 | -692,123.40 |
