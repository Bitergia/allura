from allura.app import Application
from allura import model
from allura.tests.unit import WithDatabase
from allura.tests.unit.patches import fake_app_patch
from allura.tests.unit.factories import create_project, create_app_config


class TestInstall(WithDatabase):
    patches = [fake_app_patch]

    def test_that_it_creates_a_discussion(self):
        original_discussion_count = self.discussion_count()
        install_app()
        assert self.discussion_count() == original_discussion_count + 1

    def discussion_count(self):
        return model.Discussion.query.find().count()


class TestDefaultDiscussion(WithDatabase):
    patches = [fake_app_patch]

    def setUp(self):
        super(TestDefaultDiscussion, self).setUp()
        install_app()
        self.discussion = model.Discussion.query.get(
            shortname='my_mounted_app')

    def test_that_it_has_a_description(self):
        description = self.discussion.description
        assert description == 'Forum for my_mounted_app comments'

    def test_that_it_has_a_name(self):
        assert self.discussion.name == 'my_mounted_app Discussion'

    def test_that_its_shortname_is_taken_from_the_project(self):
        assert self.discussion.shortname == 'my_mounted_app'


class TestAppDefaults(WithDatabase):
    patches = [fake_app_patch]

    def setUp(self):
        super(TestAppDefaults, self).setUp()
        self.app = install_app()

    def test_that_it_has_an_empty_sidebar_menu(self):
        assert self.app.sidebar_menu() == []

    def test_that_it_denies_access_for_everything(self):
        assert not self.app.has_access(model.User.anonymous(), 'any.topic')


def install_app():
    project = create_project('myproject')
    app_config = create_app_config(project, 'my_mounted_app')
    # XXX: Remove project argument to install; it's redundant
    app = Application(project, app_config)
    app.install(project)
    return app

