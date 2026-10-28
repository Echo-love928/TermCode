"""供应商列表的构建，不向界面传递认证信息。"""

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from termcode.config import ProviderConfig
from termcode.tui.view import clean_text


def provider_list(providers: list[ProviderConfig]) -> OptionList:
    secrets = [cfg.api_key for cfg in providers]
    return OptionList(
        *(
            Option(clean_label(cfg, secrets), id=str(index))
            for index, cfg in enumerate(providers)
        ),
        id="providers",
    )


def clean_label(cfg: ProviderConfig, secrets: list[str]) -> Text:
    return Text(clean_text(f"{cfg.name} ({cfg.model})", secrets))
