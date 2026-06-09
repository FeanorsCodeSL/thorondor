# Cluster-Semantic Goldens

Regenerate the reviewed `cluster-semantic@1` goldens only when deliberately
accepting behavior drift:

```powershell
C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe scripts\gen_cluster_semantic_golden.py
```

Review the JSON diff before accepting it. Boundary-altering changes should
normally create a new `strategy_version` such as `cluster-semantic@2`, not rewrite
the `@1` contract silently.
