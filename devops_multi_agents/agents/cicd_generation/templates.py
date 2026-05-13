"""Static pipeline template generators for each CI/CD provider."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .models import CICDProvider, PipelineStage, ServiceContext, to_posix
from .stack_profiles import StackCommandProfile

if TYPE_CHECKING:
    from .user_input import ProjectConfig


def indent_lines(lines: tuple[str, ...] | list[str], spaces: int, as_list: bool = False) -> str:
    indent = " " * spaces
    if as_list:
        return "\n".join(f"{indent}- {line}" for line in lines)
    return "\n".join(f"{indent}{line}" for line in lines)


def _github_setup_steps(runtime_family: str, config: "ProjectConfig | None" = None) -> str:
    if runtime_family == "node":
        node_ver = (getattr(config, "node_version", None) or "24") if config else "24"
        return f"""      - uses: actions/setup-node@v4
        with:
          node-version: '{node_ver}'"""
    if runtime_family == "java":
        java_ver = (getattr(config, "java_version", None) or "17") if config else "17"
        java_dist = (getattr(config, "java_distribution", None) or "temurin") if config else "temurin"
        return f"""      - uses: actions/setup-java@v4
        with:
          distribution: '{java_dist}'
          java-version: '{java_ver}'"""
    if runtime_family == "python":
        py_ver = (getattr(config, "python_version", None) or "3.12") if config else "3.12"
        return f"""      - uses: actions/setup-python@v5
        with:
          python-version: '{py_ver}'"""
    if runtime_family == "dotnet":
        return """      - uses: actions/setup-dotnet@v4
        with:
          dotnet-version: '8.0.x'"""
    if runtime_family == "go":
        return """      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'"""
    return ""


def _setup_commands_for_runtime(runtime_family: str) -> tuple[str, ...]:
    label_map = {"node": "Node.js", "java": "Java", "python": "Python", "dotnet": ".NET", "go": "Go"}
    return (f"echo 'Using {label_map.get(runtime_family, 'default')} runtime'",)


def _docker_commands() -> tuple[str, ...]:
    return (
        "if [ -f Dockerfile ]; then",
        '  if [ -n "${REGISTRY_USERNAME:-}" ] && [ -n "${REGISTRY_PASSWORD:-}" ]; then',
        '    echo "$REGISTRY_PASSWORD" | docker login "$REGISTRY_HOST" -u "$REGISTRY_USERNAME" --password-stdin',
        "  fi",
        '  docker build -f Dockerfile -t "$IMAGE_TAG" .',
        '  docker push "$IMAGE_TAG"',
        "else",
        "  echo 'Dockerfile not found; skipping image build and push.'",
        "fi",
    )


def _security_scan_commands() -> tuple[str, ...]:
    """Shell-based Trivy scan for providers that run Docker natively (GitLab, Azure, Bitbucket, Jenkins)."""
    return (
        'if [ -n "${IMAGE_TAG:-}" ]; then',
        '  docker run --rm aquasec/trivy:0.57.1 image --no-progress --severity HIGH,CRITICAL --exit-code 1 "$IMAGE_TAG"',
        "else",
        "  echo 'IMAGE_TAG is empty; skipping image scan.'",
        "fi",
    )


# ---------------------------------------------------------------------------
# GitHub Actions
# ---------------------------------------------------------------------------

def github_actions_template(
    service: ServiceContext,
    profile: StackCommandProfile,
    config: "ProjectConfig | None" = None,
) -> str:
    # Extract project config values (fall back to safe defaults)
    main_branch = (getattr(config, "main_branch", None) or "main") if config else "main"
    registry_host_default = (getattr(config, "registry_host", None) or "ghcr.io") if config else "ghcr.io"
    reg_user_secret = (getattr(config, "registry_secret_username", None) or "REGISTRY_USERNAME") if config else "REGISTRY_USERNAME"
    reg_pass_secret = (getattr(config, "registry_secret_password", None) or "REGISTRY_PASSWORD") if config else "REGISTRY_PASSWORD"

    rel_path = to_posix(service.path)
    working_dir = (
        f"${{{{ github.workspace }}}}/{rel_path}"
        if rel_path not in {".", ""}
        else "${{{{ github.workspace }}}}"
    )

    setup_steps = _github_setup_steps(profile.runtime_family, config)
    runtime_setup_block = indent_lines(_setup_commands_for_runtime(profile.runtime_family), spaces=10)
    test_block = indent_lines(profile.test, spaces=10)
    build_block = indent_lines(profile.build, spaces=10)
    package_block = indent_lines(profile.package + _docker_commands(), spaces=10)
    deploy_block = indent_lines(profile.deploy, spaces=10)

    # Artifact paths prefixed with service rel path
    artifact_block = indent_lines(
        tuple(
            f"{rel_path}/{p}" if rel_path and not p.startswith(rel_path) else p
            for p in profile.artifact_paths
        ),
        spaces=12,
    )

    # NOTE: artifact upload is placed INSIDE the package job so the runner still has
    # the build outputs on disk.  A separate upload-artifact job would run on a fresh
    # runner and cannot access files produced by a previous job.
    #
    # Security scan uses aquasecurity/trivy-action@master (official action that pulls
    # and runs Trivy) instead of `docker run aquasec/trivy` so the image does not need
    # to be present locally — it is fetched from the registry using TRIVY_USERNAME /
    # TRIVY_PASSWORD env vars.

    return f"""name: {service.slug}-pipeline
on:
  push:
    branches: [ {main_branch} ]
  pull_request:

env:
  REGISTRY_HOST: ${{{{ vars.REGISTRY_HOST || '{registry_host_default}' }}}}
  REGISTRY_USERNAME: ${{{{ secrets.{reg_user_secret} }}}}
  REGISTRY_PASSWORD: ${{{{ secrets.{reg_pass_secret} }}}}
  IMAGE_NAME: {service.slug}
  IMAGE_TAG: ${{{{ vars.REGISTRY_HOST || '{registry_host_default}' }}}}/${{{{ github.repository_owner }}}}/{service.slug}:${{{{ github.sha }}}}
  ENABLE_DEPLOY: ${{{{ vars.ENABLE_DEPLOY || 'false' }}}}
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true

jobs:
  test:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: {working_dir}
    steps:
      - uses: actions/checkout@v4
{setup_steps}
      - name: Runtime setup info
        run: |
{runtime_setup_block}
      - name: Unit tests
        run: |
{test_block}

  build:
    needs: test
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: {working_dir}
    steps:
      - uses: actions/checkout@v4
{setup_steps}
      - name: Build
        run: |
{build_block}

  package:
    needs: build
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: {working_dir}
    steps:
      - uses: actions/checkout@v4
{setup_steps}
      - name: Package + Docker build/push
        run: |
{package_block}
      - name: Upload build artifacts
        uses: actions/upload-artifact@v4
        with:
          name: {service.slug}-artifact
          path: |
{artifact_block}
          if-no-files-found: warn

  security-scan:
    needs: package
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Download build artifacts
        uses: actions/download-artifact@v4
        with:
          name: {service.slug}-artifact
          path: artifact-output
        continue-on-error: true
      - name: Filesystem security scan
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: fs
          scan-ref: .
          format: table
          exit-code: '0'
          ignore-unfixed: true
          severity: HIGH,CRITICAL

  deploy:
    if: ${{{{ github.ref == 'refs/heads/{main_branch}' && vars.ENABLE_DEPLOY == 'true' }}}}
    needs: security-scan
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: {working_dir}
    steps:
      - uses: actions/checkout@v4
      - name: Deploy
        run: |
{deploy_block}
"""


# ---------------------------------------------------------------------------
# GitLab CI
# ---------------------------------------------------------------------------

def gitlab_ci_template(
    service: ServiceContext,
    profile: StackCommandProfile,
    config: "ProjectConfig | None" = None,
) -> str:
    main_branch = (getattr(config, "main_branch", None) or "main") if config else "main"
    working_dir = to_posix(service.path)
    test_script = indent_lines(profile.test, spaces=4, as_list=True)
    build_script = indent_lines(profile.build, spaces=4, as_list=True)
    package_script = indent_lines(profile.package + _docker_commands(), spaces=4, as_list=True)
    security_script = indent_lines(_security_scan_commands(), spaces=4, as_list=True)
    deploy_script = indent_lines(profile.deploy, spaces=4, as_list=True)
    artifact_paths = indent_lines(profile.artifact_paths, spaces=6, as_list=True)

    return f"""stages:
  - {PipelineStage.TEST.value}
  - {PipelineStage.BUILD.value}
  - {PipelineStage.PACKAGE.value}
  - {PipelineStage.UPLOAD_ARTIFACT.value}
  - {PipelineStage.SECURITY_SCAN.value}
  - {PipelineStage.DEPLOY.value}

variables:
  REGISTRY_HOST: "${{REGISTRY_HOST}}"
  REGISTRY_USERNAME: "${{REGISTRY_USERNAME}}"
  REGISTRY_PASSWORD: "${{REGISTRY_PASSWORD}}"
  IMAGE_NAME: "{service.slug}"
  IMAGE_TAG: "${{REGISTRY_HOST}}/${{CI_PROJECT_PATH}}/{service.slug}:${{CI_COMMIT_SHA}}"
  ENABLE_DEPLOY: "${{ENABLE_DEPLOY:-false}}"

default:
  before_script:
    - cd "{working_dir}"

test_{service.slug}:
  stage: {PipelineStage.TEST.value}
  script:
{test_script}
  rules:
    - if: '$CI_PIPELINE_SOURCE == "push"'
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

build_{service.slug}:
  stage: {PipelineStage.BUILD.value}
  script:
{build_script}
  needs: ["test_{service.slug}"]

package_{service.slug}:
  stage: {PipelineStage.PACKAGE.value}
  script:
{package_script}
  needs: ["build_{service.slug}"]

upload_artifact_{service.slug}:
  stage: {PipelineStage.UPLOAD_ARTIFACT.value}
  script:
    - echo "Uploading artifacts for {service.slug}"
  artifacts:
    name: "{service.slug}-artifact"
    paths:
{artifact_paths}
  needs: ["package_{service.slug}"]

security_scan_{service.slug}:
  stage: {PipelineStage.SECURITY_SCAN.value}
  script:
{security_script}
  needs: ["upload_artifact_{service.slug}"]

deploy_{service.slug}:
  stage: {PipelineStage.DEPLOY.value}
  script:
{deploy_script}
  needs: ["security_scan_{service.slug}"]
  rules:
    - if: '$CI_COMMIT_BRANCH == "{main_branch}" && $ENABLE_DEPLOY == "true"'
      when: on_success
    - when: never
"""


# ---------------------------------------------------------------------------
# Azure DevOps
# ---------------------------------------------------------------------------

def azure_pipelines_template(
    service: ServiceContext,
    profile: StackCommandProfile,
    config: "ProjectConfig | None" = None,
) -> str:
    main_branch = (getattr(config, "main_branch", None) or "main") if config else "main"
    working_dir = to_posix(service.path)
    test_block = indent_lines(profile.test, spaces=14)
    build_block = indent_lines(profile.build, spaces=14)
    package_block = indent_lines(profile.package + _docker_commands(), spaces=14)
    security_block = indent_lines(_security_scan_commands(), spaces=14)
    deploy_block = indent_lines(profile.deploy, spaces=14)
    artifact_paths_block = indent_lines(profile.artifact_paths, spaces=18)

    return f"""trigger:
  branches:
    include:
      - {main_branch}

pr:
  branches:
    include:
      - {main_branch}

pool:
  vmImage: ubuntu-latest

variables:
  REGISTRY_HOST: $(REGISTRY_HOST)
  REGISTRY_USERNAME: $(REGISTRY_USERNAME)
  REGISTRY_PASSWORD: $(REGISTRY_PASSWORD)
  IMAGE_NAME: {service.slug}
  IMAGE_TAG: $(REGISTRY_HOST)/{service.slug}:$(Build.SourceVersion)
  ENABLE_DEPLOY: $(ENABLE_DEPLOY)
  SERVICE_PATH: {working_dir}

stages:
  - stage: Test
    jobs:
      - job: Test_{service.slug}
        steps:
          - checkout: self
          - script: |
{test_block}
            workingDirectory: $(SERVICE_PATH)
            displayName: Unit tests

  - stage: Build
    dependsOn: Test
    jobs:
      - job: Build_{service.slug}
        steps:
          - checkout: self
          - script: |
{build_block}
            workingDirectory: $(SERVICE_PATH)
            displayName: Build

  - stage: Package
    dependsOn: Build
    jobs:
      - job: Package_{service.slug}
        steps:
          - checkout: self
          - script: |
{package_block}
            workingDirectory: $(SERVICE_PATH)
            displayName: Package and Docker publish

  - stage: UploadArtifact
    dependsOn: Package
    jobs:
      - job: UploadArtifact_{service.slug}
        steps:
          - task: PublishBuildArtifacts@1
            inputs:
              PathtoPublish: |
{artifact_paths_block}
              ArtifactName: {service.slug}-artifact
              publishLocation: Container

  - stage: SecurityScan
    dependsOn: UploadArtifact
    jobs:
      - job: SecurityScan_{service.slug}
        steps:
          - script: |
{security_block}
            displayName: Security scan

  - stage: Deploy
    dependsOn: SecurityScan
    condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/{main_branch}'), eq(variables['ENABLE_DEPLOY'], 'true'))
    jobs:
      - job: Deploy_{service.slug}
        steps:
          - checkout: self
          - script: |
{deploy_block}
            workingDirectory: $(SERVICE_PATH)
            displayName: Deploy
"""


# ---------------------------------------------------------------------------
# Bitbucket Pipelines
# ---------------------------------------------------------------------------

def bitbucket_pipelines_template(
    service: ServiceContext,
    profile: StackCommandProfile,
    config: "ProjectConfig | None" = None,
) -> str:
    test_block = indent_lines(profile.test, spaces=12, as_list=True)
    build_block = indent_lines(profile.build, spaces=12, as_list=True)
    package_block = indent_lines(profile.package + _docker_commands(), spaces=12, as_list=True)
    security_block = indent_lines(_security_scan_commands(), spaces=12, as_list=True)
    deploy_block = indent_lines(
        (
            'if [ "${ENABLE_DEPLOY:-false}" = "true" ]; then',
            *profile.deploy,
            "else",
            "  echo 'Deployment disabled; skipping deploy stage.'",
            "fi",
        ),
        spaces=12,
        as_list=True,
    )
    artifact_block = indent_lines(profile.artifact_paths, spaces=12, as_list=True)
    working_dir = to_posix(service.path)

    return f"""image: atlassian/default-image:4

options:
  docker: true

pipelines:
  branches:
    main:
      - step:
          name: Test {service.slug}
          script:
            - cd "{working_dir}"
{test_block}

      - step:
          name: Build {service.slug}
          script:
            - cd "{working_dir}"
{build_block}

      - step:
          name: Package {service.slug}
          services:
            - docker
          script:
            - cd "{working_dir}"
{package_block}

      - step:
          name: Upload Artifact {service.slug}
          artifacts:
{artifact_block}
          script:
            - echo "Artifacts prepared for {service.slug}"

      - step:
          name: Security Scan {service.slug}
          services:
            - docker
          script:
{security_block}

      - step:
          name: Deploy {service.slug}
          script:
            - cd "{working_dir}"
{deploy_block}

  pull-requests:
    "**":
      - step:
          name: Validate {service.slug}
          script:
            - cd "{working_dir}"
{test_block}
            - echo "Build preview"
{build_block}
"""


# ---------------------------------------------------------------------------
# Jenkins
# ---------------------------------------------------------------------------

def jenkins_template(
    service: ServiceContext,
    profile: StackCommandProfile,
    config: "ProjectConfig | None" = None,
) -> str:
    working_dir = to_posix(service.path)
    test_cmds = "\\n".join(profile.test)
    build_cmds = "\\n".join(profile.build)
    package_cmds = "\\n".join(profile.package)
    deploy_cmds = "\\n".join(profile.deploy)

    return f"""pipeline {{
    agent any

    environment {{
        REGISTRY_HOST     = credentials('registry-host')
        REGISTRY_USERNAME = credentials('registry-username')
        REGISTRY_PASSWORD = credentials('registry-password')
        IMAGE_NAME        = '{service.slug}'
        IMAGE_TAG         = "${{REGISTRY_HOST}}/${{IMAGE_NAME}}:${{BUILD_NUMBER}}"
        ENABLE_DEPLOY     = '${{params.ENABLE_DEPLOY}}'
    }}

    parameters {{
        booleanParam(name: 'ENABLE_DEPLOY', defaultValue: false, description: 'Enable deployment stage')
    }}

    stages {{
        stage('Test') {{
            steps {{
                dir('{working_dir}') {{
                    sh '''{test_cmds}'''
                }}
            }}
        }}

        stage('Build') {{
            steps {{
                dir('{working_dir}') {{
                    sh '''{build_cmds}'''
                }}
            }}
        }}

        stage('Package') {{
            steps {{
                dir('{working_dir}') {{
                    sh '''{package_cmds}'''
                    script {{
                        if (fileExists('Dockerfile')) {{
                            sh 'docker build -t $IMAGE_TAG .'
                            sh 'docker push $IMAGE_TAG'
                        }}
                    }}
                }}
            }}
        }}

        stage('Upload Artifact') {{
            steps {{
                archiveArtifacts artifacts: '{", ".join(profile.artifact_paths)}', allowEmptyArchive: true
            }}
        }}

        stage('Security Scan') {{
            steps {{
                sh '''
                    if [ -n "${{IMAGE_TAG:-}}" ]; then
                        docker run --rm aquasec/trivy:0.57.1 image --no-progress --severity HIGH,CRITICAL --exit-code 1 "$IMAGE_TAG"
                    else
                        echo "IMAGE_TAG is empty; skipping scan."
                    fi
                '''
            }}
        }}

        stage('Deploy') {{
            when {{
                allOf {{
                    branch 'main'
                    expression {{ params.ENABLE_DEPLOY == true }}
                }}
            }}
            steps {{
                dir('{working_dir}') {{
                    sh '''{deploy_cmds}'''
                }}
            }}
        }}
    }}

    post {{
        failure {{
            echo 'Pipeline failed — check logs for details.'
        }}
        success {{
            echo 'Pipeline completed successfully.'
        }}
    }}
}}
"""


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

TEMPLATE_DISPATCH = {
    CICDProvider.GITHUB: github_actions_template,
    CICDProvider.GITLAB: gitlab_ci_template,
    CICDProvider.AZURE_DEVOPS: azure_pipelines_template,
    CICDProvider.BITBUCKET: bitbucket_pipelines_template,
    CICDProvider.JENKINS: jenkins_template,
}


def build_static_pipeline(
    provider: CICDProvider,
    service: ServiceContext,
    profile: StackCommandProfile,
    config: "ProjectConfig | None" = None,
) -> str:
    fn = TEMPLATE_DISPATCH.get(provider)
    if fn is None:
        raise ValueError(f"Unsupported provider template '{provider.value}'")
    return fn(service, profile, config)
