FROM condaforge/miniforge3:latest

LABEL maintainer="RioAlerta-MCP"
LABEL description="FastMCP Server for multitemporal monitoring of illegal aggregate extraction in Argentine rivers"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Instalar dependencias C++ nativas geoespaciales vía conda-forge
RUN conda install -y -c conda-forge \
    python=3.11 \
    gdal \
    rasterio \
    geopandas \
    pyproj \
    shapely \
    scipy \
    numpy \
    matplotlib-base \
    pillow \
    scikit-learn \
    && conda clean -afy

# Instalar dependencias Python alineadas con pyproject.toml
RUN pip install --no-cache-dir \
    "fastmcp>=0.4.0" \
    "pydantic>=2.7.0" \
    "pydantic-settings>=2.2.0" \
    opencv-python-headless \
    httpx \
    "pystac-client>=0.9.0" \
    "planetary-computer>=1.0.0" \
    "boto3>=1.34.0"

COPY pyproject.toml README.md .env.example ./
COPY src/ ./src/
COPY tests/ ./tests/

RUN pip install --no-cache-dir --no-deps -e . \
    && mkdir -p /app/data/raw /app/data/processed /app/data/reports \
    && python -c "from src.server import tool_server_status; assert tool_server_status()['status'] == 'ok'"

CMD ["python", "-m", "src.server"]
