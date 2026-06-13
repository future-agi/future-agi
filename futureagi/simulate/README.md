# Simulate App

Django app for managing simulation scenarios with organization-based multi-tenancy.

## Features

- ✅ **UUID Primary Keys**: All scenarios use UUID as primary key for better security and scalability
- ✅ **Organization-based Multi-tenancy**: Users can only access scenarios from their organization
- ✅ **Paginated API**: Efficient pagination using `ExtendedPageNumberPagination`
- ✅ **Strong Search**: Full-text search across name, source, and scenario_type fields
- ✅ **Soft Deletion**: Uses BaseModel's soft deletion functionality
- ✅ **Authentication Required**: All endpoints require valid authentication

## API Endpoints

### List & Create Scenarios
```
GET/POST /simulate/api/scenarios/
```

**Query Parameters:**
- `search`: Search string for filtering scenarios
- `limit`: Items per page (default: 10)
- `page`: Page number (default: 1)

### Scenario Detail Operations
```
GET/PUT/DELETE /simulate/api/scenarios/<uuid>/
```

### Test Execution Reproducibility
```
GET /simulate/test-executions/<uuid>/reproducibility/
```

Returns a replay passport and rerun preflight report for a test execution.

The response includes:
- `current.passport`: deterministic hashes for the replay inputs and runtime state
- `current.replay_plan`: replay inputs, replay key, and readiness issues
- `preflight`: replay status plus input drift from the stored start snapshot
- `score_change_diagnosis`: likely cause of score movement from replay-input drift
- `stabilization_plan`: ranked actions to make the rerun comparable before launch

The rerun modal uses the stabilization plan to show whether the rerun is low,
medium, or high risk. This helps teams avoid attributing an eval score change to
model behavior when prompt, agent, scenario, eval, or execution options changed.

## Example Usage

### Create a new scenario
```bash
POST /simulate/api/scenarios/
{
    "name": "User Authentication Flow",
    "source": "Login and signup workflow diagram",
    "scenario_type": "graph"
}
```

### Response (with UUID)
```json
{
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "User Authentication Flow",
    "source": "Login and signup workflow diagram",
    "scenario_type": "graph",
    "scenario_type_display": "Graph",
    "organization": "123e4567-e89b-12d3-a456-426614174000",
    "created_at": "2024-01-01T12:00:00Z",
    "updated_at": "2024-01-01T12:00:00Z",
    "deleted": false,
    "deleted_at": null
}
```

### Get paginated scenarios with search
```bash
GET /simulate/api/scenarios/?search=authentication&limit=5&page=1
```

### Update a scenario
```bash
PUT /simulate/api/scenarios/550e8400-e29b-41d4-a716-446655440000/
{
    "name": "Updated Authentication Flow",
    "scenario_type": "script"
}
```

### Delete a scenario (soft delete)
```bash
DELETE /simulate/api/scenarios/550e8400-e29b-41d4-a716-446655440000/
```

## Model Fields

- **id**: UUID primary key (auto-generated)
- **name**: CharField(255) - Name of the scenario
- **source**: TextField - Source content or reference
- **scenario_type**: CharField with TextChoices - Type of scenario (graph, script, dataset)
  - Uses `Scenarios.ScenarioTypes` enum: GRAPH, SCRIPT, DATASET
  - Default value: GRAPH ('graph')
- **organization**: ForeignKey to Organization
- **created_at**: DateTimeField (auto-generated)
- **updated_at**: DateTimeField (auto-updated)
- **deleted**: BooleanField (soft deletion flag)
- **deleted_at**: DateTimeField (deletion timestamp)

## String Representation

The model's `__str__` method returns just the scenario name for cleaner display:
```python
scenario = Scenarios.objects.create(name="My Scenario", ...)
str(scenario)  # Returns: "My Scenario"
```

**Note:** Type information is still available via `get_scenario_type_display()` and included in API responses through the `scenario_type_display` field.

## Search Functionality

The search feature performs case-insensitive regex matching across:
- Scenario name
- Source content
- Scenario type

Example:
```bash
# Find all scenarios with "flow" in name, source, or type
GET /simulate/api/scenarios/?search=flow

# Find scenarios containing "auth"
GET /simulate/api/scenarios/?search=auth
```

## Organization Isolation

- Users can only access scenarios from their organization
- When creating scenarios, the organization is automatically set to the authenticated user's organization
- Cross-organization access is blocked at the API level

## TextChoices Implementation

The scenario_type field uses Django's `models.TextChoices` for better type safety and IDE support:

```python
class ScenarioTypes(models.TextChoices):
    GRAPH = 'graph', 'Graph'
    SCRIPT = 'script', 'Script'
    DATASET = 'dataset', 'Dataset'
```

**Usage:**
```python
# Create scenario with enum value
scenario = Scenarios.objects.create(
    name="Test",
    source="Content",
    scenario_type=Scenarios.ScenarioTypes.SCRIPT,
    organization=org
)

# Access choices
choices = Scenarios.ScenarioTypes.choices
# [('graph', 'Graph'), ('script', 'Script'), ('dataset', 'Dataset')]

# Get display name
scenario.get_scenario_type_display()  # Returns: 'Script'

# String representation (simplified)
str(scenario)  # Returns just the name: 'Test'
```

**Note:** The `get_scenario_type_display()` method is auto-generated by Django but may show linter warnings. Type ignore comments are added where needed.
