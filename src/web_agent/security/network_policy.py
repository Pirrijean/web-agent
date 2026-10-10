from web_agent.core.schemas import SecurityProfile
from web_agent.security.origins import parse_url_origin, OriginError, is_private_or_local_host, Origin

class NetworkPolicyValidator:
    """Validador de tráfico web (Amenaza 2 - Destinos no autorizados e Inyección SSRF)."""
    
    def __init__(self, profile: SecurityProfile):
        self.profile = profile
        self.allowed_origins: set[Origin] = set()
        
        # Pre-parse allowed origins for fast lookup
        for origin_str in self.profile.allowed_origins:
            self.allowed_origins.add(parse_url_origin(origin_str))

    def is_url_allowed(self, target_url: str) -> bool:
        """Verifica si la URL destino es segura y pertenece a la lista blanca de orígenes."""
        try:
            target_origin = parse_url_origin(target_url)
        except OriginError:
            return False
            
        if is_private_or_local_host(target_origin.host):
            return False
            
        if not self.allowed_origins:
            return False

        return target_origin in self.allowed_origins
