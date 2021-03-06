from forgetracker.model import Globals
from forgetracker.tests.unit import TrackerTestWithModel
from pylons import c
from allura.lib import helpers as h

from ming.orm.ormsession import ThreadLocalORMSession


class TestGlobalsModel(TrackerTestWithModel):
    def setUp(self):
        super(TestGlobalsModel, self).setUp()
        c.project.install_app('Tickets', 'doc-bugs')
        ThreadLocalORMSession.flush_all()

    def test_it_has_current_tracker_globals(self):
        bugs_globals = Globals.query.get(app_config_id=c.app.config._id)
        assert c.app.globals == bugs_globals
        h.set_context('test', 'doc-bugs', neighborhood='Projects')
        assert c.app.globals != bugs_globals

    def test_next_ticket_number_increments(self):
        assert Globals.next_ticket_num() == 1
        assert Globals.next_ticket_num() == 2

    def test_ticket_numbers_are_independent(self):
        assert Globals.next_ticket_num() == 1
        h.set_context('test', 'doc-bugs', neighborhood='Projects')
        assert Globals.next_ticket_num() == 1


class TestCustomFields(TrackerTestWithModel):
    def test_it_has_sortable_custom_fields(self):
        tracker_globals = globals_with_custom_fields(
            [dict(label='Iteration Number',
                  name='_iteration_number',
                  show_in_search=False),
             dict(label='Point Estimate',
                  name='_point_estimate',
                  show_in_search=True)])
        expected = [dict(sortable_name='_point_estimate_s',
                         name='_point_estimate',
                         label='Point Estimate')]
        assert tracker_globals.sortable_custom_fields_shown_in_search() == expected


def globals_with_custom_fields(custom_fields):
    c.app.globals.custom_fields = custom_fields
    ThreadLocalORMSession.flush_all()
    return c.app.globals

