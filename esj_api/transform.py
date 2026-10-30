from esj_api.fabric import COLLECTION_FIELDS, COLLECTION_LIMITS

def to_public_epre(collections: dict[str, list[dict]]) -> dict:
    """Expone los campos pedidos sin convertir fechas, unidades ni valores."""
    return {
        "origen": "fabric",
        "colecciones": {
            name: {
                "cantidad_devuelta": len(collections[name]),
                "limite_solicitado": COLLECTION_LIMITS[name],
                "items": [{field: item[field] for field in fields} for item in collections[name]],
            }
            for name, fields in COLLECTION_FIELDS.items()
        },
    }
