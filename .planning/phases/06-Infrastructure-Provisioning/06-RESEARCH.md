# Phase 6: Infrastructure Provisioning - Research

**Researched:** March 21, 2026
**Domain:** Railway cloud platform deployment with PostgreSQL+pgvector and Redis
**Confidence:** HIGH

## Summary

Railway provides a straightforward platform for deploying the grasp infrastructure stack. The recommended approach uses Railway's database templates for PostgreSQL with pgvector extension and Redis, with environment variables configured as service-level variables. Secrets should be sealed for security. Total monthly cost for a small production deployment is estimated at $5-15/month on the Hobby plan.

**Primary recommendation:** Use Railway's pgvector template for PostgreSQL and standard Redis template, configure all secrets as sealed variables, and reference database connection strings between services using Railway's variable interpolation syntax.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Railway Platform | N/A | Cloud deployment platform | Industry standard for developer-friendly cloud deployments, supports Docker containers and databases |
| PostgreSQL with pgvector | pg18 | Vector database for embeddings | Official Railway template with pgvector pre-installed, supports all required vector operations (similarity search, indexing) |
| Redis | latest | Caching and message queue | Official Railway template, reliable for Celery broker and result backend |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Railway CLI | latest | Local development and deployment | For local testing with Railway infrastructure |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Railway | Heroku | Heroku has higher costs and less flexible database options |
| Railway | Render | Render has simpler database setup but less mature vector database support |
| Railway | AWS/GCP | Much more complex setup and management overhead |

**Installation:**
```bash
# Install Railway CLI
curl -fsSL https://railway.app/install.sh | sh

# Login to Railway
railway login
```

**Version verification:** Railway templates use latest stable versions as of deployment time.

## Architecture Patterns

### Recommended Project Structure
```
Railway Project: grasp-production
├── Services:
│   ├── pgvector-db (PostgreSQL with pgvector)
│   ├── redis-cache (Redis)
│   ├── api-service (FastAPI backend)
│   └── worker-service (Celery worker)
├── Environments:
│   └── production
└── Variables:
    ├── Sealed: JWT_SECRET_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, PINECONE_API_KEY
    └── Regular: APP_ENV=production, CORS_ALLOWED_ORIGINS, etc.
```

### Database Connection Pattern
**What:** Services reference database connection variables using Railway's interpolation syntax
**When to use:** Connecting services to databases within the same Railway project
**Example:**
```bash
# In api-service variables:
DATABASE_URL=${{pgvector-db.DATABASE_URL}}
REDIS_URL=${{redis-cache.REDIS_URL}}
```

### Secrets Management Pattern
**What:** Use Railway's sealed variables for sensitive data
**When to use:** Storing API keys, JWT secrets, and other credentials
**Example:**
- Variable: `ANTHROPIC_API_KEY` (sealed)
- Value: `sk-ant-api03-...` (not visible in UI after sealing)

### Anti-Patterns to Avoid
- **Storing secrets in regular variables:** Use sealed variables for all API keys and secrets
- **Hardcoding connection strings:** Always use Railway variable references
- **Single environment for dev/prod:** Use separate Railway environments or projects

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Vector database setup | Manual PostgreSQL + pgvector installation | Railway pgvector template | Template handles extension installation, configuration, and optimization |
| Redis configuration | Manual Redis deployment | Railway Redis template | Official template with proper networking and persistence |
| Secrets management | Custom encryption/storage | Railway sealed variables | Built-in security with audit trails and access controls |

**Key insight:** Railway's managed database templates eliminate the operational complexity of running databases in production.

## Common Pitfalls

### Pitfall 1: Incorrect Variable References
**What goes wrong:** Using wrong syntax for variable interpolation, causing connection failures
**Why it happens:** Railway's syntax `${{SERVICE.VARIABLE}}` is specific and case-sensitive
**How to avoid:** Always test variable references in Railway dashboard before deployment
**Warning signs:** Services failing to start with connection errors

### Pitfall 2: Unsealed Secrets
**What goes wrong:** API keys visible in Railway dashboard, potential security breach
**Why it happens:** Forgetting to seal sensitive variables
**How to avoid:** Immediately seal all API keys and JWT secrets after creation
**Warning signs:** Variables showing actual values instead of "sealed"

### Pitfall 3: Environment Confusion
**What goes wrong:** Dev and prod environments sharing the same Railway project
**Why it happens:** Single project used for multiple environments
**How to avoid:** Use separate Railway projects or environments for dev/staging/prod
**Warning signs:** Accidental data mixing between environments

## Code Examples

Verified patterns from Railway documentation:

### Database Connection Setup
```bash
# Railway variable references in service configuration
DATABASE_URL=${{pgvector-db.DATABASE_URL}}
LANGGRAPH_CHECKPOINT_URL=${{pgvector-db.DATABASE_URL}}
REDIS_URL=${{redis-cache.REDIS_URL}}
CELERY_BROKER_URL=${{redis-cache.REDIS_URL}}
CELERY_RESULT_BACKEND=${{redis-cache.REDIS_URL}}
```

### Environment Variable Configuration
```bash
# Sealed variables (secrets)
JWT_SECRET_KEY=<generated-secure-token>
ANTHROPIC_API_KEY=<anthropic-key>
OPENAI_API_KEY=<openai-key>
PINECONE_API_KEY=<pinecone-key>

# Regular variables
APP_ENV=production
LOG_LEVEL=INFO
CORS_ALLOWED_ORIGINS=["https://grasp.pages.dev"]
PINECONE_INDEX_NAME=grasp-cookbooks
PINECONE_ENVIRONMENT=us-east-1-aws
CELERY_TASK_TIMEOUT=600
CELERY_WORKER_CONCURRENCY=1
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual database provisioning | Railway templates | 2023+ | Eliminates infrastructure management overhead |
| Plain environment variables | Sealed variables | 2024+ | Enhanced security for secrets |
| Single project multi-env | Separate projects | Current best practice | Better isolation and security |

**Deprecated/outdated:**
- Manual pgvector installation: Use Railway template instead
- Unsealed API keys: Always seal sensitive variables

## Open Questions

1. **Cost scaling for vector data**
   - What we know: Railway charges $0.15/GB/month for storage
   - What's unclear: How vector indexes affect storage costs
   - Recommendation: Monitor usage in Railway dashboard, scale vertically before horizontal

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | Railway deployment verification |
| Config file | N/A |
| Quick run command | `railway status` |
| Full suite command | Manual verification of all services |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INFRA-01 | Railway project with Postgres+pgvector and Redis | manual | N/A | ✅ |
| INFRA-02 | All secrets configured as Railway env vars | manual | N/A | ✅ |

### Sampling Rate
- **Per task commit:** `railway status` to verify project connectivity
- **Per wave merge:** Manual verification of database connections
- **Phase gate:** All services deployed and accessible

### Wave 0 Gaps
- [ ] Railway project creation and configuration
- [ ] Database service deployment verification
- [ ] Environment variable setup validation

*(Wave 0 gaps represent infrastructure setup that must be completed before application deployment)*

## Sources

### Primary (HIGH confidence)
- [Railway Documentation](https://docs.railway.com/) - Official platform documentation
- [Railway PostgreSQL Guide](https://docs.railway.com/databases/postgresql) - Database setup instructions
- [Railway Variables Guide](https://docs.railway.com/variables) - Environment variable management
- [Railway Pricing](https://docs.railway.com/pricing) - Cost structure and plans

### Secondary (MEDIUM confidence)
- [pgvector Template](https://railway.com/deploy/3jJFCA) - Verified template for vector database
- [Redis Template](https://railway.com/deploy/redis) - Verified template for caching

### Tertiary (LOW confidence)
- Community templates and examples from Railway marketplace

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH - Based on official Railway documentation and templates
- Architecture: HIGH - Follows Railway best practices for multi-service deployments
- Pitfalls: HIGH - Common issues documented in Railway troubleshooting guides

**Research date:** March 21, 2026
**Valid until:** September 21, 2026 (Railway features stable, but check for template updates)</content>
<parameter name="filePath">/Users/aaronbaker/Desktop/Projects/grasp/.planning/phases/06-Infrastructure-Provisioning/06-RESEARCH.md