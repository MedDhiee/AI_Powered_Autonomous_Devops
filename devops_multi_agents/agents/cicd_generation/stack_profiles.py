"""Stack command profiles and aliases for CI/CD pipeline generation."""
from __future__ import annotations

from typing import Final

from .models import StackCommandProfile

DEFAULT_PROFILE = StackCommandProfile(
    runtime_family="generic",
    build=("echo 'No build step configured'",),
    test=("echo 'No test step configured'",),
    package=("echo 'No package step configured'",),
    artifact_paths=("build/**",),
    deploy=("echo 'Set deployment commands for this stack'",),
)

STACK_ALIASES: Final[dict[str, str]] = {
    "node": "nodejs",
    "nodejs": "nodejs",
    "javascript": "nodejs",
    "typescript": "nodejs",
    "angular": "nodejs",
    "react": "nodejs",
    "vue": "nodejs",
    "java": "java-maven",
    "java-maven": "java-maven",
    "maven": "java-maven",
    "java-gradle": "java-gradle",
    "gradle": "java-gradle",
    "kotlin": "java-gradle",
    "python": "python",
    "fastapi": "python",
    "flask": "python",
    "django": "python",
    "dotnet": "dotnet",
    "csharp": "dotnet",
    "aspnet": "dotnet",
    "go": "go",
    "golang": "go",
}

STACK_PROFILES: Final[dict[str, StackCommandProfile]] = {
    "nodejs": StackCommandProfile(
        runtime_family="node",
        build=("npm ci", "npm run build"),
        test=("npm ci", "npm test -- --watch=false"),
        package=("npm pack",),
        artifact_paths=("dist/**", "*.tgz"),
        deploy=(
            "echo 'Example deploy: kubectl apply -f k8s/'",
            "echo 'Example deploy: helm upgrade --install app ./chart'",
        ),
    ),
    "java-maven": StackCommandProfile(
        runtime_family="java",
        build=("mvn -B -DskipTests clean compile",),
        # Exclude Spring Boot context-load smoke tests (*ApplicationTests) — they
        # require a live database/service and break CI runners without one.
        # -DfailIfNoTests=false keeps the build green if exclusion removes every test.
        test=("mvn -B test -Dtest='!*ApplicationTests' -DfailIfNoTests=false",),
        package=("mvn -B -DskipTests package",),
        artifact_paths=("target/**",),
        deploy=(
            "echo 'Example deploy: java -jar target/*.jar'",
            "echo 'Example deploy: kubectl rollout restart deployment/backend'",
        ),
    ),
    "java-gradle": StackCommandProfile(
        runtime_family="java",
        build=("./gradlew build -x test",),
        test=("./gradlew test",),
        package=("./gradlew bootJar",),
        artifact_paths=("build/libs/**",),
        deploy=(
            "echo 'Example deploy: kubectl apply -f deploy/'",
            "echo 'Example deploy: helm upgrade --install service ./chart'",
        ),
    ),
    "python": StackCommandProfile(
        runtime_family="python",
        build=("python -m pip install --upgrade pip", "pip install -r requirements.txt"),
        test=("pytest -q",),
        package=("python -m pip install build", "python -m build"),
        artifact_paths=("dist/**",),
        deploy=(
            "echo 'Example deploy: uvicorn app.main:app --host 0.0.0.0 --port 8000'",
            "echo 'Example deploy: kubectl apply -f k8s/'",
        ),
    ),
    "dotnet": StackCommandProfile(
        runtime_family="dotnet",
        build=("dotnet restore", "dotnet build --configuration Release --no-restore"),
        test=("dotnet test --configuration Release --no-build",),
        package=("dotnet publish --configuration Release --output publish",),
        artifact_paths=("publish/**",),
        deploy=(
            "echo 'Example deploy: az webapp deploy --src-path publish'",
            "echo 'Example deploy: kubectl apply -f k8s/'",
        ),
    ),
    "go": StackCommandProfile(
        runtime_family="go",
        build=("go build ./...",),
        test=("go test ./...",),
        package=("mkdir -p dist", "go build -o dist/app ./..."),
        artifact_paths=("dist/**",),
        deploy=(
            "echo 'Example deploy: ./dist/app'",
            "echo 'Example deploy: kubectl set image deployment/app app=$IMAGE_TAG'",
        ),
    ),
}

STACK_EXAMPLES_FOR_PROMPT: Final[str] = """
- Node.js: test=`npm test -- --watch=false`, build=`npm run build`, package=`npm pack`
- Java Maven: test=`mvn -B test`, build=`mvn -B -DskipTests clean compile`, package=`mvn -B -DskipTests package`
- Java Gradle: test=`./gradlew test`, build=`./gradlew build -x test`, package=`./gradlew bootJar`
- Python: test=`pytest -q`, build=`pip install -r requirements.txt`, package=`python -m build`
- .NET: test=`dotnet test --configuration Release --no-build`, build=`dotnet build --configuration Release`, package=`dotnet publish`
- Go: test=`go test ./...`, build=`go build ./...`, package=`go build -o dist/app ./...`
""".strip()


def commands_for_stack(stack: str, config=None) -> StackCommandProfile:
    """Return the command profile for *stack*, applying user config overrides when provided."""
    from dataclasses import replace as _replace

    normalized = STACK_ALIASES.get(stack.strip().lower(), stack.strip().lower())
    profile = STACK_PROFILES.get(normalized, DEFAULT_PROFILE)

    # Apply Maven-wrapper preference from ProjectConfig.
    # Use `bash mvnw` (not `./mvnw`) so the runner doesn't need the execute bit
    # on mvnw — Git on Windows commonly drops it, producing
    # `Permission denied` (exit code 126) on Ubuntu runners.
    if config is not None and profile.runtime_family == "java" and hasattr(config, "use_mvn_wrapper"):
        if config.use_mvn_wrapper:
            def _to_mvnw(cmd: str) -> str:
                return "bash mvnw " + cmd[4:] if cmd.startswith("mvn ") else cmd

            profile = _replace(
                profile,
                build=tuple(_to_mvnw(c) for c in profile.build),
                test=tuple(_to_mvnw(c) for c in profile.test),
                package=tuple(_to_mvnw(c) for c in profile.package),
            )

    return profile
