Python 3.13.16, full run, 1200 live ticks × 500 msgs

### Incident: 60-tick blackout at tick 600 (30,000 msgs) + 0.1% packet loss

| strategy | final book exact | recovery msgs fetched | msgs applied / published | ticks in sync | p99 staleness (msgs) | max staleness | ticks to recover after blackout | CPU |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| full replay | yes | 2,232,720 | 4.19× | 10.8% | 446,500 | 457,500 | 959 | 4.86 s |
| range gap-fill | yes | 30,500 | 1.00× | 96.2% | 19,500 | 30,500 | 21 | 2.60 s |
| gap-fill + snapshot | yes | 4,962 | 0.95× | 97.7% | 380 | 31,500 | 3 | 2.53 s |

### Random packet loss 0.05% (no blackout)

| strategy | final book exact | recovery msgs fetched | msgs applied / published | ticks in sync | p99 staleness (msgs) | max staleness | ticks to recover after blackout | CPU |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| full replay | yes | 2,557,460 | 4.71× | 12.0% | 533,000 | 544,000 | – | 4.78 s |
| range gap-fill | yes | 260 | 1.00× | 98.9% | 100 | 500 | – | 2.37 s |
| gap-fill + snapshot | yes | 260 | 1.00× | 98.9% | 100 | 500 | – | 2.29 s |

### Random packet loss 0.1% (no blackout)

| strategy | final book exact | recovery msgs fetched | msgs applied / published | ticks in sync | p99 staleness (msgs) | max staleness | ticks to recover after blackout | CPU |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| full replay | yes | 2,326,220 | 4.23× | 8.1% | 458,000 | 469,000 | – | 4.62 s |
| range gap-fill | yes | 800 | 1.00× | 96.8% | 400 | 520 | – | 2.27 s |
| gap-fill + snapshot | yes | 800 | 1.00× | 96.8% | 400 | 520 | – | 2.30 s |

### Random packet loss 1% (no blackout)

| strategy | final book exact | recovery msgs fetched | msgs applied / published | ticks in sync | p99 staleness (msgs) | max staleness | ticks to recover after blackout | CPU |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| full replay | yes | 2,902,000 | 4.88× | 0.9% | 570,500 | 581,500 | – | 5.06 s |
| range gap-fill | yes | 6,440 | 1.00× | 76.5% | 520 | 520 | – | 2.36 s |
| gap-fill + snapshot | yes | 6,440 | 1.00× | 76.5% | 520 | 520 | – | 2.25 s |
