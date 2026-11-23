-- 0003: outreach_message is the delivery log. A row is claimed ('sending') before the channel
-- adapter runs; the UNIQUE approval_request_id makes each approval deliver at most once.

ALTER TABLE outreach_message DROP CONSTRAINT outreach_message_delivery_status_check;
ALTER TABLE outreach_message ADD CONSTRAINT outreach_message_delivery_status_check
    CHECK (delivery_status IN ('sending', 'sent', 'queued_for_manual_send', 'manually_sent',
                               'bounced', 'failed'));

ALTER TABLE outreach_message
    ADD COLUMN content jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN error   text;
