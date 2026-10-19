"""Readable native tables shared by the report reader and chat cards."""
from PySide6.QtGui import QColor, QTextLength, QTextTable, QTextCursor, QTextBlockFormat
from PySide6.QtWidgets import QTextBrowser


class RichBrowser(QTextBrowser):
    def setMarkdown(self, text):
        super().setMarkdown(text)
        self.document().setDocumentMargin(20)
        block = self.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            format = block.blockFormat()
            level = format.headingLevel()
            format.setTopMargin(12 if level else 4)
            format.setBottomMargin(8 if level else 6)
            format.setLineHeight(135, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            cursor.setBlockFormat(format)
            fragments = block.begin()
            while not fragments.atEnd():
                fragment = fragments.fragment()
                if fragment.isValid() and fragment.charFormat().isAnchor():
                    cursor.setPosition(fragment.position())
                    cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                    link_style = fragment.charFormat()
                    link_style.setForeground(QColor('#286759'))
                    link_style.setFontUnderline(True)
                    cursor.setCharFormat(link_style)
                fragments += 1
            block = block.next()
        def visit(frame):
            if isinstance(frame, QTextTable):
                format = frame.format()
                format.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                format.setBorder(0)
                format.setCellSpacing(1)
                format.setCellPadding(10)
                frame.setFormat(format)
                for row in range(min(frame.rows(), 100)):
                    for col in range(frame.columns()):
                        cell = frame.cellAt(row, col)
                        style = cell.format()
                        style.setBackground(QColor('#deebe4' if row == 0 else '#f4f7f2' if row % 2 else '#ffffff'))
                        if row == 0:
                            style.setFontWeight(600)
                        cell.setFormat(style)
            for child in frame.childFrames():
                visit(child)
        visit(self.document().rootFrame())
