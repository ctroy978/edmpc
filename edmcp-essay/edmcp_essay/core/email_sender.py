import asyncio
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from jinja2 import Environment, FileSystemLoader, select_autoescape, TemplateNotFound
import aiosmtplib


class EmailSender:
    """
    Handles SMTP connection and email sending with Jinja2 template support.
    Designed for sending student feedback PDFs via Brevo SMTP relay or similar providers.
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_pass: str,
        from_email: str,
        from_name: str = "Grade Reports",
        use_tls: bool = True,
        timeout: int = 30
    ):
        """
        Initialize EmailSender with SMTP configuration.

        Args:
            smtp_host: SMTP server hostname (e.g., smtp-relay.brevo.com)
            smtp_port: SMTP server port (typically 587 for STARTTLS)
            smtp_user: SMTP authentication username
            smtp_pass: SMTP authentication password
            from_email: Sender email address
            from_name: Sender display name
            use_tls: Whether to use STARTTLS (default: True)
            timeout: SMTP connection timeout in seconds (default: 30)
        """
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_pass = smtp_pass
        self.from_email = from_email
        self.from_name = from_name
        self.use_tls = use_tls
        self.timeout = timeout

        # Setup Jinja2 template environment
        template_dir = Path(__file__).parent.parent / "data" / "email_templates"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(['html', 'xml'])
        )

    def render_template(
        self,
        template_name: str,
        context: Dict[str, Any]
    ) -> Tuple[str, str]:
        """
        Renders both HTML and plain text versions of an email template.

        Args:
            template_name: Base name of template (e.g., 'default_feedback')
            context: Dictionary of variables to pass to template

        Returns:
            Tuple of (html_body, plain_body)

        Raises:
            TemplateNotFound: If template files don't exist
        """
        try:
            html_template = self.jinja_env.get_template(f"{template_name}.html.j2")
            txt_template = self.jinja_env.get_template(f"{template_name}.txt.j2")

            # Add from_name to context for use in templates
            context_with_sender = {**context, "from_name": self.from_name}

            html_body = html_template.render(**context_with_sender)
            plain_body = txt_template.render(**context_with_sender)

            return html_body, plain_body

        except TemplateNotFound as e:
            raise TemplateNotFound(
                f"Email template '{template_name}' not found. "
                f"Ensure both {template_name}.html.j2 and {template_name}.txt.j2 exist "
                f"in edmcp_essay/data/email_templates/"
            )

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_plain: str,
        attachments: Optional[List[Path]] = None
    ) -> bool:
        """
        Sends an email with optional attachments via SMTP.

        Args:
            to_email: Recipient email address
            subject: Email subject line
            body_html: HTML email body
            body_plain: Plain text email body (fallback)
            attachments: List of file paths to attach (optional)

        Returns:
            True if email sent successfully, False otherwise
        """
        try:
            # Create multipart message with proper structure for attachments
            msg = MIMEMultipart('mixed')
            msg['Subject'] = subject
            msg['From'] = f'"{self.from_name}" <{self.from_email}>'
            msg['To'] = to_email

            # Body container with plain text and HTML alternatives
            body_container = MIMEMultipart('alternative')
            body_container.attach(MIMEText(body_plain, 'plain'))
            body_container.attach(MIMEText(body_html, 'html'))
            msg.attach(body_container)

            # Attach files if provided
            if attachments:
                for attachment_path in attachments:
                    if not attachment_path.exists():
                        print(f"[EmailSender] Warning: Attachment not found: {attachment_path}")
                        continue

                    with open(attachment_path, 'rb') as f:
                        attachment = MIMEApplication(f.read())
                        attachment.add_header(
                            'Content-Disposition',
                            'attachment',
                            filename=attachment_path.name
                        )
                        msg.attach(attachment)

            # Send email via SMTP
            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user,
                password=self.smtp_pass,
                start_tls=self.use_tls,
                timeout=self.timeout
            )

            return True

        except aiosmtplib.SMTPException as e:
            print(f"[EmailSender] SMTP Error sending to {to_email}: {e}")
            return False
        except Exception as e:
            print(f"[EmailSender] Unexpected error sending to {to_email}: {e}")
            return False

    async def test_connection(self) -> bool:
        """
        Tests SMTP connection without sending an email.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            smtp = aiosmtplib.SMTP(
                hostname=self.smtp_host,
                port=self.smtp_port,
                timeout=self.timeout
            )
            await smtp.connect()

            if self.use_tls and not smtp.is_tls:
                await smtp.starttls()

            await smtp.login(self.smtp_user, self.smtp_pass)
            await smtp.quit()

            print(f"[EmailSender] SMTP connection successful to {self.smtp_host}:{self.smtp_port}")
            return True

        except Exception as e:
            print(f"[EmailSender] SMTP connection failed: {e}")
            return False
