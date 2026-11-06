# trucksim-llm: one model operating one TruckSimEnv seat, as an A2A agent on agent-env's model endpoint.
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
RUN pip install "agentenv-framework-protocol[agent]==0.1.351" "openai>=1.60,<3" "mcp>=1.25,<2" \
 && useradd --uid 10001 --create-home agent
COPY agent.py /app/agent.py
USER agent
EXPOSE 8000
CMD ["python", "/app/agent.py"]
