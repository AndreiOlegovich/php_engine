FROM python:3.11-slim

WORKDIR /app

COPY pytest_web/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Tests are live-mounted from ./pytest_web in compose; COPY keeps
# standalone `docker build` working too.
COPY pytest_web/ /app/

# One-shot run: pytest exits when done (no restart policy in compose).
# Results go to the shared allure-results volume for the allure-web service.
CMD ["sh", "-c", "pytest -q --alluredir=/allure-results --clean-alluredir"]
