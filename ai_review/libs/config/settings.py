from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, JsonConfigSettingsSource, SettingsConfigDict

from ai_review.libs.config.agent import AgentConfig
from ai_review.libs.config.artifacts import ArtifactsConfig
from ai_review.libs.config.base import get_env_config_file_or_default, get_json_config_file_or_default, get_yaml_config_file_or_default
from ai_review.libs.config.core import CoreConfig
from ai_review.libs.config.knowledge import KnowledgeConfig
from ai_review.libs.config.llm.base import LLMConfig
from ai_review.libs.config.logger import LoggerConfig
from ai_review.libs.config.prompt import PromptConfig
from ai_review.libs.config.review import ReviewConfig
from ai_review.libs.config.vcs.base import VCSConfig
from ai_review.libs.config.yaml import StrictYamlConfigSettingsSource


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="allow", env_file=get_env_config_file_or_default(), env_nested_delimiter="__", yaml_file=get_yaml_config_file_or_default(), yaml_file_encoding="utf-8", json_file=get_json_config_file_or_default())
    llm: LLMConfig
    vcs: VCSConfig
    core: CoreConfig = Field(default_factory=CoreConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    logger: LoggerConfig = Field(default_factory=LoggerConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (StrictYamlConfigSettingsSource(cls), JsonConfigSettingsSource(cls), env_settings, dotenv_settings, init_settings)


def load_settings(config_path: Path | None = None) -> Settings:
    if config_path is None:
        return Settings()
    class FileSettings(Settings):
        model_config = SettingsConfigDict(**{**Settings.model_config, "yaml_file": str(config_path)})
    return FileSettings()


class SyncBootstrapSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", yaml_file=get_yaml_config_file_or_default(), yaml_file_encoding="utf-8")
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (StrictYamlConfigSettingsSource(cls), env_settings, init_settings)


def load_sync_settings(config_path: Path | None = None) -> SyncBootstrapSettings:
    if config_path is None:
        return SyncBootstrapSettings()
    class FileSyncBootstrapSettings(SyncBootstrapSettings):
        model_config = SettingsConfigDict(**{**SyncBootstrapSettings.model_config, "yaml_file": str(config_path)})
    return FileSyncBootstrapSettings()
