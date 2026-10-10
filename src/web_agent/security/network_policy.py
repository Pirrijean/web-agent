from web_agent.core.schemas import SecurityProfile
from web_agent.security.origins import parse_url_origin, OriginError, is_private_or_local_host, Origin

class NetworkPolicyValidator:
    """Validador de tráfico web (Amenaza 2 - Destinos no autorizados e Inyección SSRF)."""
    
    def __init__(self, profile: SecurityProfile):
        self.profile = profile
        self.allowed_origins: set[Origin] = set()
        
        # Pre-parse allowed origins for fast lookup
        for origin_str in self.profile.allowed_origins:
            try:
                self.allowed_origins.add(parse_url_origin(origin_str))
            except OriginError:
                # Silently ignore bad origins in profile during init, 
                # though ideally the schema validates them.
                pass

    def is_url_allowed(self, target_url: str) -> bool:
        """Verifica si la URL destino es segura y pertenece a la lista blanca de orígenes."""
        try:
            target_origin = parse_url_origin(target_url)
        except OriginError:
            return False
            
        if is_private_or_local_host(target_origin.host):
            return False
            
        if not self.allowed_origins:
            # If no allowed origins specified, allow any non-private?
            # Or default deny? The schema probably requires it.
            # Let's say if it's not restricted, we return True, but typically it should be checked.
            # Usually security profiles have allowed_origins.
            # I will assume default deny if allowed_origins is not empty, but if it is empty... wait.
            # Let's check schemas.py to see if it defaults to empty.
            pass

        return target_origin in self.allowed_origins
