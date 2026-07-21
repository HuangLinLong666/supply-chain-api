// 查看新增整车运输网络规模
MATCH (n)
WHERE n:TransportLocation OR n:VehicleRoute OR n:RouteLeg OR n:Evidence OR n:RiskSnapshot OR n:CostEstimate
RETURN labels(n) AS labels,count(n) AS count
ORDER BY count DESC;

// 查看一条完整路线及来源
MATCH path=(origin)<-[:ORIGIN]-(route:VehicleRoute)-[:HAS_LEG]->(leg:RouteLeg)-[:TO_NODE]->(destination)
OPTIONAL MATCH (route)-[:HAS_RISK_SNAPSHOT]->(risk:RiskSnapshot)
OPTIONAL MATCH (route)-[:HAS_COST_ESTIMATE]->(cost:CostEstimate)
RETURN path,route.route_id,route.source,route.source_type,route.confidence,
       risk.risk_score,cost.most_likely
LIMIT 20;

// 查找待人工审核路线
MATCH (route:VehicleRoute)
WHERE route.deleted_at IS NULL AND coalesce(route.review_status,'pending')='pending'
RETURN route.route_id,route.origin_id,route.destination_id,route.route_type,
       route.confidence,route.is_inferred
ORDER BY route.confidence ASC;

// 软删除示例；不要 DETACH DELETE
MATCH (route:VehicleRoute {route_id:$route_id})
SET route.deleted_at=datetime(),route.route_status='deleted';

// 恢复软删除路线
MATCH (route:VehicleRoute {route_id:$route_id})
REMOVE route.deleted_at
SET route.route_status='candidate';
