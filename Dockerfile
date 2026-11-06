# TruckSimEnv's env image: the sim core, the game-env server and the static data pack, serving MCP on port 18765.
#   Build and register: agent-env trucksim setup   (or: docker build -t agentenv-trucksim-env .)
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/polakamtejas/agentenv-trucksim-plugin" \
      org.opencontainers.image.description="TruckSimEnv on AgentEnv. Road data (c) OpenStreetMap contributors (ODbL); diesel from EIA, freight flows from FAF5, weather from NOAA Storm Events."
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# The env side of agentenv-game-env needs only the protocol, MCP and pydantic; its task steps (which need the whole
# framework) run in agent-env, not here, so it is installed without its dependencies.
RUN pip install "agentenv-framework-protocol==0.1.351" "mcp>=1.25,<2" "pydantic>=2,<3" "httpx>=0.27,<1" "ortools>=9.10,<10" \
 && pip install --no-deps "agentenv-game-env @ https://github.com/earakely-scale/agentenv-game-env/archive/e9316f823ef37d9cecd90bb171ef50d9ddf375e7.tar.gz"
WORKDIR /app
COPY trucksim_core /app/trucksim_core
RUN rm -f /app/trucksim_core/data_pack
COPY src/agentenv_trucksim /app/agentenv_trucksim
COPY data/roads.json data/road_shapes.json data/diesel.json data/freight.json data/weather.json data/closures.json data/hos.json data/costs.json data/manifest.json /app/trucksim_core/data_pack/
RUN useradd --create-home --uid 10001 trucksim
USER trucksim
EXPOSE 18765
CMD ["python", "-m", "agentenv_trucksim.server"]
