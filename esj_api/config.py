import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Ruta provisional. El prefijo /api lo define host.json.
EPRE_ROUTE = "datos/epre"


class ServiceUnavailable(Exception):
    """Diagnóstico interno estático, sin secretos ni datos del consumidor."""


@dataclass(frozen=True)
class Settings:
    fabric_graphql_endpoint: str = field(repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        endpoint = os.getenv("FABRIC_GRAPHQL_ENDPOINT", "").strip()
        try:
            url = urlsplit(endpoint)
            valid = (
                url.scheme == "https"
                and bool(url.hostname)
                and url.hostname.endswith(".graphql.fabric.microsoft.com")
                and url.port in (None, 443)
                and not url.username and not url.password
                and not url.query and not url.fragment
                and url.path.endswith("/graphql")
                and not any(character.isspace() for character in endpoint)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ServiceUnavailable(
                "FABRIC_GRAPHQL_ENDPOINT debe ser una URL HTTPS válida de GraphQL de Fabric, sin credenciales ni query string."
            )
        if os.getenv("AZURE_TOKEN_CREDENTIALS", "").lower() not in {
            "azureclicredential", "managedidentitycredential"
        }:
            raise ServiceUnavailable(
                "Configurar AZURE_TOKEN_CREDENTIALS=AzureCliCredential en desarrollo o ManagedIdentityCredential en Azure."
            )
        # Mismos indicadores que usa el worker Python de Azure Functions.
        # WEBSITE_HOSTNAME también existe en Core Tools y no distingue Azure.
        hosted = "WEBSITE_INSTANCE_ID" in os.environ or "CONTAINER_NAME" in os.environ
        if hosted and os.getenv("AZURE_TOKEN_CREDENTIALS", "").lower() != "managedidentitycredential":
            raise ServiceUnavailable("En Azure la Function debe usar ManagedIdentityCredential; Azure CLI sólo está permitido en desarrollo local.")
        return cls(endpoint)
