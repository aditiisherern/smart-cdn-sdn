# smart-cdn-project
# Smart Content Delivery System

## Project Overview
An SDN-based content delivery system using Mininet,
Open vSwitch, Python and OS-Ken.

## Network Setup
- Server 1: 10.0.0.1
- Server 2: 10.0.0.2
- Server 3: 10.0.0.3
- HTTP Port: 8080
- OpenFlow: 1.3

## Person 3 Responsibilities

1. Server health monitoring.
2. Link utilization monitoring.
3. Server failure and recovery testing.
4. Network failure testing.
5. Performance measurement.
6. Throughput measurement.
7. Experiment documentation and graphs.

## Experiments

1. Normal network performance.
2. Server failure and recovery.
3. Link failure and recovery.
4. Slow link test (1 Mbps).
5. Six simultaneous HTTP requests.

## Results

All five experiments have recorded result files
inside the results/ directory.

Performance graphs and throughput measurements
are also stored in results/.

## Limitations

- Six separate client hosts are not yet configured.
- Smart versus Round-Robin comparison remains pending.
- Automatic server selection and failover require further integration.
- Throughput measurements are short local virtual-network tests.

## Conclusion

Server monitoring, link statistics collection,
failure testing and basic performance evaluation
were demonstrated successfully.
