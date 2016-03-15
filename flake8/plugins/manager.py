"""Plugin loading and management logic and classes."""
import collections
import logging

from flake8 import exceptions
from flake8 import utils
from flake8.plugins import notifier

import pkg_resources

LOG = logging.getLogger(__name__)

__all__ = (
    'Checkers',
    'Listeners',
    'Plugin',
    'PluginManager',
    'ReportFormatters',
)


class Plugin(object):
    """Wrap an EntryPoint from setuptools and other logic."""

    def __init__(self, name, entry_point):
        """"Initialize our Plugin.

        :param str name:
            Name of the entry-point as it was registered with setuptools.
        :param entry_point:
            EntryPoint returned by setuptools.
        :type entry_point:
            setuptools.EntryPoint
        """
        self.name = name
        self.entry_point = entry_point
        self._plugin = None
        self._parameters = None

    def __repr__(self):
        """Provide an easy to read description of the current plugin."""
        return 'Plugin(name="{0}", entry_point="{1}")'.format(
            self.name, self.entry_point
        )

    @property
    def parameters(self):
        """List of arguments that need to be passed to the plugin."""
        if self._parameters is None:
            self._parameters = utils.parameters_for(self)
        return self._parameters

    @property
    def plugin(self):
        """The loaded (and cached) plugin associated with the entry-point.

        This property implicitly loads the plugin and then caches it.
        """
        self.load_plugin()
        return self._plugin

    @property
    def version(self):
        """Return the version attribute on the plugin."""
        return self.plugin.version

    def execute(self, *args, **kwargs):
        r"""Call the plugin with \*args and \*\*kwargs."""
        return self.plugin(*args, **kwargs)  # pylint: disable=not-callable

    def _load(self, verify_requirements):
        # Avoid relying on hasattr() here.
        resolve = getattr(self.entry_point, 'resolve', None)
        require = getattr(self.entry_point, 'require', None)
        if resolve and require:
            if verify_requirements:
                LOG.debug('Verifying plugin "%s"\'s requirements.',
                          self.name)
                require()
            self._plugin = resolve()
        else:
            self._plugin = self.entry_point.load(
                require=verify_requirements
            )

    def load_plugin(self, verify_requirements=False):
        """Retrieve the plugin for this entry-point.

        This loads the plugin, stores it on the instance and then returns it.
        It does not reload it after the first time, it merely returns the
        cached plugin.

        :param bool verify_requirements:
            Whether or not to make setuptools verify that the requirements for
            the plugin are satisfied.
        :returns:
            Nothing
        """
        if self._plugin is None:
            LOG.info('Loading plugin "%s" from entry-point.', self.name)
            try:
                self._load(verify_requirements)
            except Exception as load_exception:
                LOG.exception(load_exception, exc_info=True)
                failed_to_load = exceptions.FailedToLoadPlugin(
                    plugin=self,
                    exception=load_exception,
                )
                LOG.critical(str(failed_to_load))
                raise failed_to_load

    def provide_options(self, optmanager, options, extra_args):
        """Pass the parsed options and extra arguments to the plugin."""
        parse_options = getattr(self.plugin, 'parse_options', None)
        if parse_options is not None:
            LOG.debug('Providing options to plugin "%s".', self.name)
            try:
                parse_options(optmanager, options, extra_args)
            except TypeError:
                parse_options(options)

    def register_options(self, optmanager):
        """Register the plugin's command-line options on the OptionManager.

        :param optmanager:
            Instantiated OptionManager to register options on.
        :type optmanager:
            flake8.options.manager.OptionManager
        :returns:
            Nothing
        """
        add_options = getattr(self.plugin, 'add_options', None)
        if add_options is not None:
            LOG.debug(
                'Registering options from plugin "%s" on OptionManager %r',
                self.name, optmanager
            )
            add_options(optmanager)
            optmanager.register_plugin(
                entry_point_name=self.name,
                name=self.plugin.name,
                version=self.plugin.version
            )


class PluginManager(object):  # pylint: disable=too-few-public-methods
    """Find and manage plugins consistently."""

    def __init__(self, namespace, verify_requirements=False):
        """Initialize the manager.

        :param str namespace:
            Namespace of the plugins to manage, e.g., 'flake8.extension'.
        :param bool verify_requirements:
            Whether or not to make setuptools verify that the requirements for
            the plugin are satisfied.
        """
        self.namespace = namespace
        self.verify_requirements = verify_requirements
        self.plugins = {}
        self.names = []
        self._load_all_plugins()

    def _load_all_plugins(self):
        LOG.info('Loading entry-points for "%s".', self.namespace)
        for entry_point in pkg_resources.iter_entry_points(self.namespace):
            name = entry_point.name
            self.plugins[name] = Plugin(name, entry_point)
            self.names.append(name)
            LOG.debug('Loaded %r for plugin "%s".', self.plugins[name], name)

    def map(self, func, *args, **kwargs):
        r"""Call ``func`` with the plugin and \*args and \**kwargs after.

        This yields the return value from ``func`` for each plugin.

        :param collections.Callable func:
            Function to call with each plugin. Signature should at least be:

            .. code-block:: python

                def myfunc(plugin):
                     pass

            Any extra positional or keyword arguments specified with map will
            be passed along to this function after the plugin. The plugin
            passed is a :class:`~flake8.plugins.manager.Plugin`.
        :param args:
            Positional arguments to pass to ``func`` after each plugin.
        :param kwargs:
            Keyword arguments to pass to ``func`` after each plugin.
        """
        for name in self.names:
            yield func(self.plugins[name], *args, **kwargs)


class PluginTypeManager(object):
    """Parent class for most of the specific plugin types."""

    namespace = None

    def __init__(self):
        """Initialize the plugin type's manager."""
        self.manager = PluginManager(self.namespace)
        self.plugins_loaded = False

    def __contains__(self, name):
        """Check if the entry-point name is in this plugin type manager."""
        LOG.debug('Checking for "%s" in plugin type manager.', name)
        return name in self.plugins

    def __getitem__(self, name):
        """Retrieve a plugin by its name."""
        LOG.debug('Retrieving plugin for "%s".', name)
        return self.plugins[name]

    def get(self, name, default=None):
        """Retrieve the plugin referred to by ``name`` or return the default.

        :param str name:
            Name of the plugin to retrieve.
        :param default:
            Default value to return.
        :returns:
            Plugin object referred to by name, if it exists.
        :rtype:
            :class:`Plugin`
        """
        if name in self:
            return self[name]
        return default

    @property
    def names(self):
        """Proxy attribute to underlying manager."""
        return self.manager.names

    @property
    def plugins(self):
        """Proxy attribute to underlying manager."""
        return self.manager.plugins

    @staticmethod
    def _generate_call_function(method_name, optmanager, *args, **kwargs):
        def generated_function(plugin):
            """Function that attempts to call a specific method on a plugin."""
            method = getattr(plugin, method_name, None)
            if (method is not None and
                    isinstance(method, collections.Callable)):
                return method(optmanager, *args, **kwargs)
        return generated_function

    def load_plugins(self):
        """Load all plugins of this type that are managed by this manager."""
        if self.plugins_loaded:
            return

        def load_plugin(plugin):
            """Call each plugin's load_plugin method."""
            return plugin.load_plugin()

        plugins = list(self.manager.map(load_plugin))
        # Do not set plugins_loaded if we run into an exception
        self.plugins_loaded = True
        return plugins

    def register_options(self, optmanager):
        """Register all of the checkers' options to the OptionManager."""
        self.load_plugins()
        call_register_options = self._generate_call_function(
            'register_options', optmanager,
        )

        list(self.manager.map(call_register_options))

    def provide_options(self, optmanager, options, extra_args):
        """Provide parsed options and extra arguments to the plugins."""
        call_provide_options = self._generate_call_function(
            'provide_options', optmanager, options, extra_args,
        )

        list(self.manager.map(call_provide_options))


class NotifierBuilderMixin(object):  # pylint: disable=too-few-public-methods
    """Mixin class that builds a Notifier from a PluginManager."""

    def build_notifier(self):
        """Build a Notifier for our Listeners.

        :returns:
            Object to notify our listeners of certain error codes and
            warnings.
        :rtype:
            :class:`~flake8.notifier.Notifier`
        """
        notifier_trie = notifier.Notifier()
        for name in self.names:
            notifier_trie.register_listener(name, self.manager[name])
        return notifier_trie


class Checkers(PluginTypeManager):
    """All of the checkers registered through entry-ponits."""

    namespace = 'flake8.extension'

    def checks_expecting(self, argument_name):
        """Retrieve checks that expect an argument with the specified name.

        Find all checker plugins that are expecting a specific argument.
        """
        for plugin in self.plugins.values():
            if argument_name == plugin.parameters[0]:
                yield plugin

    @property
    def ast_plugins(self):
        """List of plugins that expect the AST tree."""
        plugins = getattr(self, '_ast_plugins', [])
        if not plugins:
            plugins = list(self.checks_expecting('tree'))
            self._ast_plugins = plugins
        return plugins

    @property
    def logical_line_plugins(self):
        """List of plugins that expect the logical lines."""
        plugins = getattr(self, '_logical_line_plugins', [])
        if not plugins:
            plugins = list(self.checks_expecting('logical_line'))
            self._logical_line_plugins = plugins
        return plugins

    @property
    def physical_line_plugins(self):
        """List of plugins that expect the physical lines."""
        plugins = getattr(self, '_physical_line_plugins', [])
        if not plugins:
            plugins = list(self.checks_expecting('physical_line'))
            self._physical_line_plugins = plugins
        return plugins


class Listeners(PluginTypeManager, NotifierBuilderMixin):
    """All of the listeners registered through entry-points."""

    namespace = 'flake8.listen'


class ReportFormatters(PluginTypeManager):
    """All of the report formatters registered through entry-points."""

    namespace = 'flake8.report'
