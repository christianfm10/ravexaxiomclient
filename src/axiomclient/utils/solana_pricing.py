"""
Módulo para obtener y actualizar precios históricos de Solana desde CoinGecko API.

Ejemplo de respuesta de la API:
{
  "prices": [[1768953600000, 125.81], [1769040000000, 129.34]],
  "market_caps": [[1768953600000, 71176869803.27], ...],
  "total_volumes": [[1768953600000, 6120599149.83], ...]
}
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
import httpx

load_dotenv()


class SolanaPricingManager:
    """Gestiona la obtención y actualización de precios históricos de Solana."""

    API_URL = "https://api.coingecko.com/api/v3/coins/solana/market_chart"
    DEFAULT_DATA_FILE = "solana_prices.json"

    def __init__(
        self,
        data_file: Optional[str] = None,
        db_manager=None,
        use_db: bool = True,
    ):
        """
        Inicializa el gestor de precios.

        Args:
            data_file: Ruta al archivo JSON donde se guardan los datos.
                      Si no se especifica, usa el archivo por defecto.
            db_manager: Instancia de AsyncDatabaseManager para guardar en PostgreSQL
            use_db: Si True, guarda en base de datos además del JSON
        """
        self.data_file = Path(data_file) if data_file else Path(self.DEFAULT_DATA_FILE)
        self.db_manager = db_manager
        self.use_db = use_db

    def fetch_prices(self, days: int = 30, vs_currency: str = "usd") -> Dict:
        """
        Obtiene los precios de Solana desde la API de CoinGecko.

        Args:
            days: Número de días de historial a obtener (máximo según API)
            vs_currency: Moneda de referencia (por defecto USD)

        Returns:
            Diccionario con prices, market_caps y total_volumes

        Raises:
            httpx.RequestError: Si hay un error en la petición HTTP
        """
        params = {
            "vs_currency": vs_currency,
            "days": days,
            "interval": "daily",
            "precision": 2,
        }

        response = httpx.get(self.API_URL, params=params)
        response.raise_for_status()

        return response.json()

    def load_existing_data(self) -> Optional[Dict]:
        """
        Carga los datos existentes desde el archivo JSON.

        Returns:
            Diccionario con los datos guardados o None si no existe el archivo
        """
        if not self.data_file.exists():
            return None

        try:
            with open(self.data_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error al leer el archivo {self.data_file}: {e}")
            return None

    def save_data(self, data: Dict):
        """
        Guarda los datos en el archivo JSON.

        Args:
            data: Diccionario con los datos a guardar
        """
        with open(self.data_file, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Datos guardados en {self.data_file}")

    async def save_data_to_db(self, data: Dict):
        """
        Guarda los datos en la base de datos PostgreSQL.

        Args:
            data: Diccionario con formato {"YYYY-MM-DD": precio}
        """
        if not self.use_db or not self.db_manager:
            return

        try:
            saved_count = await self.db_manager.save_solana_prices(data)
            print(f"Guardados {saved_count} precios en la base de datos")
        except Exception as e:
            print(f"Error al guardar en base de datos: {e}")

    async def load_data_from_db(self) -> Optional[Dict]:
        """
        Carga los datos desde la base de datos PostgreSQL.

        Returns:
            Diccionario con formato {"YYYY-MM-DD": precio} o None si hay error
        """
        if not self.use_db or not self.db_manager:
            return None

        try:
            return await self.db_manager.get_all_solana_prices()
        except Exception as e:
            print(f"Error al cargar desde base de datos: {e}")
            return None

    def _convert_to_simple_format(self, api_data: Dict) -> Dict[str, float]:
        """
        Convierte los datos de la API al formato simplificado {"YYYY-MM-DD": precio}.

        Args:
            api_data: Datos en formato API con "prices": [[timestamp, precio], ...]

        Returns:
            Diccionario en formato {"YYYY-MM-DD": precio}
        """
        result = {}
        if "prices" in api_data:
            for timestamp_ms, price in api_data["prices"]:
                date = datetime.fromtimestamp(timestamp_ms / 1000)
                date_str = date.strftime("%Y-%m-%d")
                result[date_str] = price
        return result

    def get_last_date(self, data: Dict) -> Optional[str]:
        """
        Obtiene la última fecha guardada en los datos.

        Args:
            data: Diccionario con los datos históricos en formato {"YYYY-MM-DD": precio}

        Returns:
            Fecha en formato "YYYY-MM-DD" o None si no hay datos
        """
        if not data:
            return None

        # Obtener la fecha más reciente
        dates = sorted(data.keys())
        return dates[-1] if dates else None

    async def get_last_date_from_db(self) -> Optional[str]:
        """
        Obtiene la última fecha guardada en la base de datos.

        Returns:
            Fecha en formato "YYYY-MM-DD" o None si no hay datos
        """
        if not self.use_db or not self.db_manager:
            return None

        try:
            return await self.db_manager.get_last_solana_price_date()
        except Exception as e:
            print(f"Error al obtener última fecha de base de datos: {e}")
            return None

    def merge_data(self, existing_data: Dict, new_data: Dict) -> Dict:
        """
        Combina datos existentes con nuevos datos, evitando duplicados.

        Args:
            existing_data: Datos históricos existentes en formato {"YYYY-MM-DD": precio}
            new_data: Nuevos datos obtenidos de la API (formato API)

        Returns:
            Diccionario con los datos combinados en formato simplificado
        """
        if not existing_data:
            return self._convert_to_simple_format(new_data)

        # Convertir nuevos datos al formato simplificado
        new_formatted = self._convert_to_simple_format(new_data)

        # Combinar, los nuevos datos sobrescriben los existentes si hay conflicto
        result = existing_data.copy()
        result.update(new_formatted)

        return result

    def get_days_since_last_update(self, last_date_str: str) -> int:
        """
        Calcula cuántos días han pasado desde la última fecha.

        Args:
            last_date_str: Fecha en formato "YYYY-MM-DD"

        Returns:
            Número de días desde la última actualización
        """
        last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
        current_date = datetime.now()
        delta = current_date - last_date
        return delta.days

    async def initialize_data(self, days: int = 30):
        """
        Inicializa el archivo de datos con el historial de los últimos N días.

        Args:
            days: Número de días de historial a obtener (por defecto 30)
        """
        print(f"Obteniendo datos de los últimos {days} días...")
        api_data = self.fetch_prices(days=days)

        # Convertir al formato simplificado
        simple_data = self._convert_to_simple_format(api_data)
        self.save_data(simple_data)

        # Guardar en base de datos si está habilitado
        if self.use_db and self.db_manager:
            await self.save_data_to_db(simple_data)

        # Mostrar resumen
        if simple_data:
            dates = sorted(simple_data.keys())
            print(f"Datos obtenidos desde {dates[0]} hasta {dates[-1]}")
            print(f"Total de registros: {len(simple_data)}")

    async def update_data(self):
        """
        Actualiza los datos desde la última fecha guardada hasta la fecha actual.
        Si no existe el archivo, inicializa con 30 días de historial.
        Prioriza la base de datos si está disponible, luego el JSON.
        """
        # Intentar cargar desde base de datos primero si está habilitado
        existing_data = None
        if self.use_db and self.db_manager:
            existing_data = await self.load_data_from_db()
            if existing_data:
                print(
                    f"Datos cargados desde base de datos: {len(existing_data)} registros"
                )

        # Si no hay datos en DB, intentar cargar desde JSON
        if existing_data is None:
            existing_data = self.load_existing_data()
            if existing_data:
                print(f"Datos cargados desde JSON: {len(existing_data)} registros")

        if existing_data is None:
            print("No se encontraron datos previos. Inicializando...")
            await self.initialize_data()
            return

        last_date = self.get_last_date(existing_data)

        if last_date is None:
            print("Los datos existentes están vacíos. Inicializando...")
            await self.initialize_data()
            return

        days_since_last = self.get_days_since_last_update(last_date)

        if days_since_last <= 0:
            print("Los datos ya están actualizados.")
            return

        print(f"Actualizando datos. Días desde última actualización: {days_since_last}")

        # Obtener datos desde la última fecha
        # Agregamos 1 día extra para asegurar que capturamos todos los datos
        new_data = self.fetch_prices(days=days_since_last + 1)

        # Combinar con datos existentes
        merged_data = self.merge_data(existing_data, new_data)

        # Guardar datos actualizados en JSON
        self.save_data(merged_data)

        # Guardar en base de datos si está habilitado
        if self.use_db and self.db_manager:
            await self.save_data_to_db(merged_data)

        # Mostrar resumen
        if merged_data:
            dates = sorted(merged_data.keys())
            print(f"Datos actualizados desde {dates[0]} hasta {dates[-1]}")
            print(f"Total de registros: {len(merged_data)}")

    async def get_statistics(self) -> dict:
        """
        Obtiene estadísticas de los precios guardados.

        Returns:
            Diccionario con estadísticas
        """
        stats = {}

        # Estadísticas de JSON
        json_data = self.load_existing_data()
        if json_data:
            dates = sorted(json_data.keys())
            prices = list(json_data.values())
            stats["json"] = {
                "total_records": len(json_data),
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
                "min_price": min(prices) if prices else None,
                "max_price": max(prices) if prices else None,
                "avg_price": sum(prices) / len(prices) if prices else None,
            }

        # Estadísticas de base de datos
        if self.use_db and self.db_manager:
            try:
                db_stats = await self.db_manager.get_solana_price_stats()
                stats["database"] = db_stats
            except Exception as e:
                print(f"Error al obtener estadísticas de base de datos: {e}")

        return stats


def main():
    """Función principal para ejecutar desde línea de comandos."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Gestiona precios históricos de Solana"
    )
    parser.add_argument(
        "--file",
        "-f",
        default="solana_prices.json",
        help="Ruta al archivo JSON (por defecto: solana_prices.json)",
    )
    parser.add_argument(
        "--init",
        "-i",
        action="store_true",
        help="Inicializa el archivo con 30 días de historial",
    )
    parser.add_argument(
        "--update",
        "-u",
        action="store_true",
        help="Actualiza los datos desde la última fecha guardada",
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=30,
        help="Número de días de historial (solo para --init)",
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL"),
        help="URL de la base de datos PostgreSQL (por defecto: variable de entorno DATABASE_URL)",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="No usar base de datos, solo JSON",
    )
    parser.add_argument(
        "--stats",
        "-s",
        action="store_true",
        help="Muestra estadísticas de los precios guardados",
    )

    args = parser.parse_args()

    # Ejecutar con asyncio
    asyncio.run(async_main(args))


async def async_main(args):
    """Función principal asíncrona."""
    db_manager = None
    use_db = not args.no_db

    # Inicializar database manager si se proporciona URL
    if use_db and args.db_url:
        from axiomclient.database.async_session import AsyncDatabaseManager

        db_manager = AsyncDatabaseManager(database_url=args.db_url)
        await db_manager.create_tables()
        print(f"Conectado a base de datos: {args.db_url}")

    manager = SolanaPricingManager(
        data_file=args.file, db_manager=db_manager, use_db=use_db
    )

    try:
        if args.stats:
            stats = await manager.get_statistics()
            print("\n=== Estadísticas de Precios de Solana ===\n")

            if "json" in stats:
                print("JSON:")
                for key, value in stats["json"].items():
                    print(f"  {key}: {value}")
                print()

            if "database" in stats:
                print("Base de datos:")
                for key, value in stats["database"].items():
                    print(f"  {key}: {value}")
        elif args.init:
            await manager.initialize_data(days=args.days)
        elif args.update:
            await manager.update_data()
        else:
            # Por defecto, actualizar si existe o inicializar si no existe
            await manager.update_data()
    finally:
        # Cerrar conexiones de base de datos
        if db_manager:
            await db_manager.close()


if __name__ == "__main__":
    main()
