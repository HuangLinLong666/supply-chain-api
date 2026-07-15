// 港口总数
MATCH (p:Port) RETURN count(p) AS port_count;

// 坐标完整性
MATCH (p:Port)
RETURN count(p) AS total,
       count(CASE WHEN p.latitude IS NOT NULL AND p.longitude IS NOT NULL THEN 1 END) AS with_coordinates,
       count(CASE WHEN p.latitude IS NULL OR p.longitude IS NULL THEN 1 END) AS missing_coordinates;

// 已有天气风险和高风险港口
MATCH (p:Port) WHERE p.weather_risk_score IS NOT NULL
RETURN p.name,p.country,p.weather_risk_score,p.weather_risk_level,p.weather_risk_summary,p.weather_updated_at
ORDER BY p.weather_risk_score DESC;

// 最近快照
MATCH (p:Port)-[:HAS_WEATHER_SNAPSHOT]->(w:WeatherRiskSnapshot)
RETURN p.name,w.snapshot_id,w.observed_at,w.current_risk_score,w.current_risk_level,w.trend
ORDER BY w.observed_at DESC LIMIT 50;

// 天气风险传播到海运路段
MATCH (s:RouteSegment)
WHERE coalesce(s.mode,s.routeMode)='sea' AND s.route_weather_risk IS NOT NULL
RETURN s.segmentId,s.fromNodeName,s.toNodeName,s.origin_port_weather_risk,s.destination_port_weather_risk,s.route_weather_risk,s.route_weather_updated_at
ORDER BY s.route_weather_risk DESC;
