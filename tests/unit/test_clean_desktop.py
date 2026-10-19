import os
import pytest
from newspaper_agent.database.research_repository import ResearchRepository


@pytest.mark.skipif(os.name != 'nt', reason='Saved credentials require Windows')
def test_clean_layout_keeps_controls_and_unified_save(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.theme import STYLE
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setStyleSheet(STYLE)
    repo = ResearchRepository(tmp_path / 'clean.sqlite3')
    repo.register('reader', 'password123')
    window = Window(repo, 'reader')
    assert window.global_stop.isHidden() and window.stop_button.isHidden()
    assert window.retry_button.isHidden()
    for toggle in (window.news_filters_toggle, window.connection_options_toggle,
        window.connection_management_toggle, window.request_options_toggle,
        window.profile_preferences_toggle, window.profile_proposals_toggle, window.source_management_toggle):
        assert not toggle.isChecked()
    assert window.connection_protocol.parentWidget().isHidden()
    window.connection_options_toggle.click()
    assert not window.connection_protocol.parentWidget().isHidden()
    window.new_connection_button.click()
    window.connection_name.setText('界面离线测试')
    window.connection_url.setText('https://example.org/v1')
    window.connection_model.setText('test-model')
    window.model_key.setText('dummy-layout-test-only')
    window.save_connection_button.click()
    identity = window.provider.currentData()
    assert len(window.connections.list('reader')) == 1
    window.connection_model.setText('updated-test-model')
    window.save_connection_button.click()
    assert window.provider.currentData() == identity
    assert len(window.connections.list('reader')) == 1
    assert window.connections.get('reader', identity)['model'] == 'updated-test-model'
    window.connection_options_toggle.setChecked(False)
    window.navigate(5)
    window.show()
    app.processEvents()
    window.grab().save(str(tmp_path / 'clean-settings.png'))
    window.navigate(2)
    app.processEvents()
    window.grab().save(str(tmp_path / 'clean-profile.png'))
    window.close()
