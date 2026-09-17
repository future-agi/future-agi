# TraceErrorAnalysisAgent with Memory and Database Storage

This enhanced version of the TraceErrorAnalysisAgent integrates both episodic and semantic memory capabilities with persistent database storage for comprehensive error analysis and learning.

## 🧠 Memory System

### Episodic Memory
- **What it stores**: Past trace executions, tool usage patterns, error occurrences
- **Key features**:
  - Recent trace analysis (last 30 days)
  - Tool usage patterns and error frequencies
  - Similar trace detection for learning from past experiences
  - Error span tracking and categorization

### Semantic Memory
- **What it stores**: Learned patterns, human feedback, improvement recommendations
- **Key features**:
  - Error taxonomy learnings by category
  - Improvement recommendations based on patterns
  - Context usage patterns (retrieval, chunk usage)
  - Auto-fix suggestions for common errors

## 🗄️ Database Storage

### Core Tables

#### `TraceErrorAnalysis`
Stores comprehensive analysis results:
- Overall scores and metrics
- Error counts by impact level
- Detailed scoring breakdown
- Memory context and insights

#### `TraceErrorDetail`
Stores individual error information:
- Error classification (category/subcategory)
- Impact and urgency levels
- Location spans and evidence
- Recommendations and trace impact

#### `TraceErrorGroup`
Stores grouped error information:
- Error type and affected spans
- Combined impact and descriptions
- Error count per group

#### `ErrorPattern`
Stores learned error patterns:
- Pattern frequency and confidence
- Context information (tool, span type)
- Recommendations and auto-fix suggestions
- Resolution tracking

#### `ErrorMemory`
Stores episodic and semantic memory:
- Memory type and key identification
- Memory data and metadata
- Access tracking and usage statistics

## 🚀 Usage Examples

### Basic Analysis
```python
from agentic_eval.agenthub.traceerroragent.traceerror import TraceErrorAnalysisAgent

# Basic analysis without memory
agent = TraceErrorAnalysisAgent(
    trace_id="your-trace-id",
    enable_memory=False,
    save_to_db=True
)
result = agent.summarize()
```

### Memory-Enhanced Analysis
```python
# Analysis with memory enhancement
agent = TraceErrorAnalysisAgent(
    trace_id="your-trace-id",
    enable_memory=True,
    save_to_db=True
)
result = agent.summarize()
```

### Database Operations
```python
from agentic_eval.agenthub.traceerroragent.analysis_service import TraceErrorAnalysisService

service = TraceErrorAnalysisService("project-id")

# Get recent analyses
recent = service.get_recent_analyses(days=30, limit=10)

# Get error statistics
stats = service.get_error_statistics(days=30)

# Search with filters
filters = {
    'min_score': 3.0,
    'max_errors': 5,
    'priority': 'HIGH',
    'memory_enhanced': True
}
filtered = service.search_analyses(filters)
```

## 🔍 Search and Filtering

### Analysis Filters
- **Score range**: `min_score`, `max_score`
- **Error count**: `min_errors`, `max_errors`
- **Priority level**: `priority` (HIGH, MEDIUM, LOW)
- **Memory enhancement**: `memory_enhanced` (boolean)
- **Date range**: `date_from`, `date_to`

### Error Pattern Queries
- **By category**: Filter patterns by error category
- **By frequency**: Get most frequent error patterns
- **By confidence**: Filter by confidence score
- **By resolution status**: Show resolved/unresolved patterns

## 📊 Analytics and Insights

### Error Statistics
- Total analyses and average scores
- Error counts by category and subcategory
- Impact level distribution
- Trend analysis over time

### Pattern Learning
- Automatic pattern detection from repeated errors
- Confidence scoring based on frequency
- Recommendation generation for common issues
- Auto-fix suggestion tracking

### Memory Analytics
- Memory usage statistics
- Pattern recognition effectiveness
- Learning rate tracking
- Memory access patterns

## 🔧 Configuration

### Memory Settings
```python
# Memory window (days)
memory_window = 30

# Similarity threshold for trace matching
similarity_threshold = 0.3

# Pattern confidence thresholds
min_confidence = 0.5
```

### Database Settings
```python
# Save analysis results
save_to_db = True

# Memory enhancement
enable_memory = True

# Agent version tracking
agent_version = "1.0"
```

## 📈 Benefits

### 1. **Improved Error Detection**
- Memory-enhanced analysis provides better context
- Pattern recognition from past errors
- Similar trace comparison for better accuracy

### 2. **Learning and Adaptation**
- Automatic pattern learning from repeated errors
- Confidence scoring for pattern reliability
- Continuous improvement through feedback

### 3. **Comprehensive Tracking**
- Persistent storage of all analysis results
- Historical trend analysis
- Performance metrics tracking

### 4. **Advanced Filtering**
- Multi-dimensional search capabilities
- Complex filter combinations
- Real-time analytics and reporting

### 5. **Memory-Driven Insights**
- Episodic memory for trace similarity
- Semantic memory for error patterns
- Contextual recommendations

## 🛠️ Integration

### With Existing Systems
- Compatible with current trace analysis workflows
- Optional memory enhancement (can be disabled)
- Backward compatible with existing APIs

### Database Migration
```bash
# Create database tables
python manage.py makemigrations tracer
python manage.py migrate
```

### API Integration
```python
# REST API endpoints for analysis results
GET /api/trace-error-analysis/{trace_id}/
GET /api/trace-error-analysis/statistics/
POST /api/trace-error-analysis/search/
```

## 📋 Example Workflow

1. **Run Analysis**: Execute trace error analysis with memory
2. **Save Results**: Automatically save to database
3. **Learn Patterns**: Update error patterns for future use
4. **Generate Insights**: Create recommendations based on patterns
5. **Track Progress**: Monitor improvements over time

## 🔮 Future Enhancements

### Planned Features
- **Real-time Analysis**: Live error detection during trace execution
- **Predictive Analytics**: Forecast potential errors before they occur
- **Auto-fix Implementation**: Automatic error resolution suggestions
- **Advanced ML Integration**: Machine learning for pattern recognition
- **Collaborative Learning**: Cross-project pattern sharing

### Advanced Memory Features
- **Long-term Memory**: Extended memory windows for deeper learning
- **Contextual Memory**: Context-aware pattern recognition
- **Adaptive Learning**: Dynamic confidence scoring
- **Memory Optimization**: Intelligent memory pruning and optimization

## 📚 API Reference

### TraceErrorAnalysisAgent
```python
class TraceErrorAnalysisAgent:
    def __init__(self, trace_id, llm=None, enable_memory=True, save_to_db=True)
    def summarize() -> Dict[str, Any]
    def get_memory_context() -> Dict[str, Any]
```

### TraceErrorAnalysisService
```python
class TraceErrorAnalysisService:
    def save_analysis_result(trace_id: str, result: Dict) -> TraceErrorAnalysis
    def get_analysis_by_trace(trace_id: str) -> Optional[TraceErrorAnalysis]
    def get_error_statistics(days: int = 30) -> Dict[str, Any]
    def search_analyses(filters: Dict) -> List[TraceErrorAnalysis]
    def get_error_patterns(category: str = None) -> List[ErrorPattern]
```

## 🤝 Contributing

### Development Setup
1. Clone the repository
2. Install dependencies
3. Run database migrations
4. Execute example scripts

### Testing
```bash
# Run unit tests
python -m pytest tests/

# Run integration tests
python -m pytest tests/integration/

# Run example scripts
python example_usage.py
```

## 📄 License

This project is part of the core-backend system and follows the same licensing terms.

---

**Note**: This enhanced system provides a comprehensive solution for trace error analysis with memory capabilities, enabling continuous learning and improvement of error detection accuracy over time. 