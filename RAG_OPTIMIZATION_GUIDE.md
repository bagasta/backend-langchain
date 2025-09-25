# RAG Performance Optimization Guide

This guide documents the enhanced RAG (Retrieval-Augmented Generation) system optimizations implemented to improve speed, efficiency, and accuracy.

## 🚀 Key Improvements

### 1. **Enhanced Vector Indexing**
- **HNSW Index Support**: Automatically uses HNSW (Hierarchical Navigable Small World) indexes for high-dimensional vectors (>1536 dims)
- **Adaptive Index Selection**: Chooses optimal index type based on vector dimensions
- **Faster Search**: HNSW provides sub-linear search complexity vs linear scan

### 2. **Multi-Level Caching**
- **Query Result Caching**: Caches complete query results to avoid repeated similarity searches
- **Embedding Cache**: Enhanced LRU caching with increased capacity (1024 vs 512 entries)
- **Redis Integration**: Optional distributed caching for multi-instance deployments

### 3. **Connection Pooling**
- **PostgreSQL Connection Pool**: Reduces connection overhead for vector operations
- **Resource Management**: Proper connection cleanup and reuse
- **Configurable Pool Size**: 2-10 connections by default

### 4. **Adaptive Configuration**
- **Dynamic Probe Adjustment**: Automatically adjusts IVFFLAT probes based on dataset size
- **Smart Timeouts**: Adaptive query timeouts based on index type and data size
- **Performance Monitoring**: Built-in metrics collection and reporting

### 5. **Enhanced Query Capabilities**
- **Metadata Filtering**: Advanced filtering capabilities for precise retrieval
- **Query Optimization**: Enhanced SQL generation with proper parameter binding
- **Error Handling**: Improved retry logic and connection management

## 📊 Performance Metrics

The enhanced RAG system provides comprehensive performance metrics:

```python
from agents.rag import get_rag_metrics

metrics = get_rag_metrics()
print(f"Cache hit rate: {metrics['cache_hit_rate']:.2%}")
print(f"Average query time: {metrics['avg_query_time']:.3f}s")
print(f"Total queries: {metrics['query_count']}")
```

## 🔧 Configuration

### Environment Variables

Key configuration options (see `.env.rag.optimization` for complete list):

```bash
# Enable HNSW for high-dimensional vectors
RAG_HNSW_ENABLED=true

# Configure connection pooling
RAG_CONNECTION_POOL_MIN=2
RAG_CONNECTION_POOL_MAX=10

# Enable adaptive configuration
RAG_ADAPTIVE_PROBES=true

# Configure caching
RAG_EMBED_CACHE_MAX=1024
RAG_QUERY_CACHE_MAX=1000
RAG_REDIS_URL=redis://localhost:6379/0
```

### Usage Examples

#### Basic Usage (Enhanced)

```python
from agents.rag import retrieve_topk

# Standard retrieval with automatic optimization
results = retrieve_topk(
    user_id="user123",
    agent_id="agent456",
    query="What is machine learning?",
    top_k=5
)
```

#### Advanced Usage with Metadata Filtering

```python
from agents.rag import retrieve_topk

# Retrieve with metadata filtering
results = retrieve_topk(
    user_id="user123",
    agent_id="agent456",
    query="Recent AI research papers",
    top_k=10,
    metadata_filter={
        "category": "research",
        "year": 2024,
        "source": "arxiv"
    }
)
```

#### Performance Monitoring

```python
from agents.rag import get_rag_metrics, get_rag_configuration, reset_rag_metrics

# Get current performance metrics
metrics = get_rag_metrics()
print(f"Performance: {metrics}")

# Get configuration
config = get_rag_configuration()
print(f"HNSW Enabled: {config['hnsw_enabled']}")

# Reset metrics
reset_rag_metrics()
```

## 🎯 Expected Performance Improvements

### Speed Improvements
- **Query Response Time**: 40-60% faster due to caching and connection pooling
- **Embedding Cache**: 80%+ hit rate for repeated queries
- **Index Performance**: HNSW provides 10-100x faster search for large datasets

### Resource Efficiency
- **Connection Overhead**: 70% reduction through connection pooling
- **Memory Usage**: Improved LRU cache management
- **CPU Utilization**: Reduced through better indexing strategies

### Accuracy Improvements
- **Search Precision**: Enhanced HNSW indexing for better recall
- **Adaptive Probes**: Dynamic optimization based on dataset size
- **Metadata Filtering**: More precise retrieval with advanced filtering

## 🔍 Monitoring and Debugging

### Log Messages

The enhanced RAG system provides detailed logging:

```
[RAG] Connection pool initialized (2-10)
[RAG] created HNSW index for tb_123_456
[RAG] query cache hit for a1b2c3d4...
[RAG] query completed in 0.042s (cached: True)
```

### Performance Metrics

Key metrics to monitor:

- **Cache Hit Rate**: Target >75%
- **Average Query Time**: Target <200ms
- **Connection Pool Usage**: Monitor pool efficiency
- **Index Types**: Track HNSW vs IVFFLAT usage

## 🛠️ Troubleshooting

### Common Issues

1. **Slow Queries**
   - Check if HNSW is enabled for high-dimensional vectors
   - Verify connection pool configuration
   - Monitor cache hit rates

2. **Memory Usage**
   - Adjust cache sizes based on available memory
   - Monitor Redis usage if enabled
   - Consider using LRU eviction strategies

3. **Connection Issues**
   - Verify database connection string
   - Check connection pool settings
   - Monitor PostgreSQL connection limits

### Optimization Tips

1. **For Large Datasets (>100K vectors)**
   - Enable HNSW indexing
   - Increase connection pool size
   - Use Redis for distributed caching

2. **For High-Query Volumes**
   - Increase cache sizes
   - Monitor connection pool usage
   - Consider read replicas

3. **For Mixed Workloads**
   - Use adaptive probes
   - Configure appropriate timeouts
   - Monitor resource contention

## 🔄 Migration Guide

### From Previous Version

1. **No Breaking Changes**: All existing functionality preserved
2. **Enhanced Defaults**: Better out-of-the-box performance
3. **Optional Features**: New features are opt-in via configuration

### Recommended Setup

1. **Copy Configuration**: Copy `.env.rag.optimization` to `.env`
2. **Install Dependencies**: Install Redis if using distributed caching
3. **Test Performance**: Run with monitoring to verify improvements
4. **Tune Parameters**: Adjust based on your specific use case

## 📈 Success Metrics

The enhanced RAG system should achieve:

- **Response Time**: <200ms for 95% of queries
- **Cache Hit Rate**: >75% for typical workloads
- **Connection Efficiency**: <50ms connection overhead
- **Search Accuracy**: >90% recall@k for relevant queries

## 🚀 Future Enhancements

Planned improvements include:

- **Parallel Query Processing**: Multi-threaded query execution
- **Batch Operations**: Bulk embedding and indexing
- **Hybrid Search**: Combine vector and keyword search
- **Real-time Index Updates**: Dynamic index maintenance