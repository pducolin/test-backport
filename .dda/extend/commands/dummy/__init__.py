from __future__ import annotations


from typing import TYPE_CHECKING

from dda.cli.base import dynamic_command, pass_app


if TYPE_CHECKING:
    from dda.cli.application import Application


@dynamic_command(
    short_help="Dummy command",
    context_settings={"help_option_names": [], "ignore_unknown_options": True},
)
@pass_app
def cmd(
    app: Application,
) -> None:
    """
    Dummy command.
    """
    app.display("Dummy command")
