# Particle Counter API Simulator

A lightweight simulator that serves particle count sample data over a GraphQL API. Useful for developing and testing cleanroom monitoring integrations without access to physical hardware.

## Overview

The simulator exposes a GraphQL endpoint with these capabilities:

1. **Authentication** - Login with credentials, receive a JWT for subsequent requests
2. **Sample Queries** - Retrieve paginated particle count records
3. **Sample Control** - Start/stop sampling runs (simulated)
4. **Data Management** - Clear the sample buffer

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the simulator
python -m particle_sim --port 8443

# The GraphQL endpoint is available at https://localhost:8443/graphql
```

## API

### Authentication

```graphql
mutation authenticate($username: String!, $password: String!) {
  authenticate(username: $username, password: $password)
}
```

Returns a JWT string. Default credentials: `admin` / `password`.

All subsequent requests must include the header:
```
Authorization: Bearer <jwt>
```

### Get Samples

```graphql
query getSamples($cursor: String, $limit: Int) {
  getSamples(cursor: $cursor, limit: $limit) {
    samples {
      id
      deviceId
      deviceName
      sequenceNumber
      startedAt
      completedAt
      status
      config {
        mode
        durationSeconds
        repeatCount
        volume {
          units
          value
        }
      }
      results {
        channels {
          sizeUm
          particleCount
        }
        totalVolume {
          units
          value
        }
        environment {
          flowRate {
            average { units value }
          }
          temperature {
            average { units value }
          }
          humidity {
            average { units value }
          }
        }
      }
      operator {
        name
        username
        role
      }
    }
    pagination {
      nextCursor
      hasMore
    }
  }
}
```

**Variables:**

| Name | Type | Description |
|------|------|-------------|
| `limit` | Int | Max records to return (default: all) |
| `cursor` | String | Opaque cursor for pagination |

### Start Sampling

```graphql
mutation startSampling($input: SamplingInput!) {
  startSampling(input: $input)
}
```

**SamplingInput:**

| Field | Type | Description |
|-------|------|-------------|
| `channels` | [Float!]! | Channel sizes in microns (e.g. `[0.3, 0.5, 1.0, 5.0]`) |
| `mode` | String! | `"TIMED"` or `"VOLUME"` |
| `durationSeconds` | Int | Seconds per sample (when mode is TIMED) |
| `volume` | Object | `{ units: "L", value: Float }` (when mode is VOLUME) |
| `repeatCount` | Int | Number of samples to take (0 = continuous until stopped) |
| `delaySeconds` | Int | Seconds to wait before first sample |
| `pauseSeconds` | Int | Seconds to wait between repeated samples |

Returns `true` on success.

### Stop Sampling

```graphql
mutation stopSampling {
  stopSampling
}
```

Returns `true` on success.

### Clear Samples

```graphql
mutation clearSamples {
  clearSamples
}
```

Requires an admin-role user. Returns `true` on success.

## Simulator Behavior

The simulator pre-generates a configurable number of sample records with realistic random data:

- **Channels:** Standard ISO 21501 sizes (0.3, 0.5, 1.0, 3.0, 5.0, 10.0 microns)
- **Counts:** Random integers following a decreasing distribution as particle size increases
- **Flow rate:** ~28.3 LPM (1 CFM) with minor variance
- **Temperature/Humidity:** Randomized within typical cleanroom ranges
- **Timestamps:** Sequential, spaced by the configured sample duration

When sampling is started via the API, the simulator generates new records after the configured duration elapses.

## Configuration

Environment variables or CLI flags:

| Setting | Default | Description |
|---------|---------|-------------|
| `PORT` | 8443 | HTTPS port |
| `SEED_SAMPLES` | 50 | Number of pre-generated sample records |
| `DEVICE_ID` | SIM-001 | Serial number returned in samples |
| `DEVICE_NAME` | Simulator | Device name returned in samples |
| `DURATION` | 60 | Default sample duration in seconds |
| `CHANNELS` | 0.3,0.5,1,3,5,10 | Default channel sizes in microns |

## Sample Response Example

```json
{
  "data": {
    "getSamples": {
      "samples": [
        {
          "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
          "deviceId": "SIM-001",
          "deviceName": "Simulator",
          "sequenceNumber": 1,
          "startedAt": "2026-08-28T10:00:00.000Z",
          "completedAt": "2026-08-28T10:01:00.000Z",
          "status": "COMPLETED",
          "config": {
            "mode": "TIMED",
            "durationSeconds": 60,
            "repeatCount": 1,
            "volume": { "units": "L", "value": 28.3 }
          },
          "results": {
            "channels": [
              { "sizeUm": 0.3, "particleCount": 1523 },
              { "sizeUm": 0.5, "particleCount": 842 },
              { "sizeUm": 1.0, "particleCount": 215 },
              { "sizeUm": 3.0, "particleCount": 42 },
              { "sizeUm": 5.0, "particleCount": 8 },
              { "sizeUm": 10.0, "particleCount": 1 }
            ],
            "totalVolume": { "units": "L", "value": 28.3 },
            "environment": {
              "flowRate": {
                "average": { "units": "LPM", "value": 28.31 }
              },
              "temperature": {
                "average": { "units": "C", "value": 22.4 }
              },
              "humidity": {
                "average": { "units": "%RH", "value": 45.2 }
              }
            }
          },
          "operator": {
            "name": "Admin User",
            "username": "admin",
            "role": "ADMIN"
          }
        }
      ],
      "pagination": {
        "nextCursor": "eyJpZCI6MX0=",
        "hasMore": false
      }
    }
  }
}
```

## Project Structure

```
particle_sim/
  __init__.py
  __main__.py        # CLI entry point
  server.py          # HTTPS + GraphQL server setup
  schema.py          # GraphQL type definitions and resolvers
  auth.py            # JWT generation and validation
  data.py            # Sample data generation
  config.py          # Configuration loading
requirements.txt
```

## License

MIT
