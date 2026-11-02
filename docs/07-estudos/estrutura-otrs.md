# Estrutura OTRS relevante para o estudo NOC - ATACADO

Inventário obtido de `information_schema`; estimativas de linhas são metadata. Sem dump de conteúdo.

Banco: `otrs`

| Tabela/View | Tipo | Estimativa de linhas | Campos candidatos | Relevância inferida |
|---|---|---:|---|---|
| `article` | BASE TABLE | 803661 | `id` (bigint(20)), `ticket_id` (bigint(20)), `article_sender_type_id` (smallint(6)), `communication_channel_id` (bigint(20)), `is_visible_for_customer` (smallint(6)), `search_index_needs_rebuild` (smallint(6)), `insert_fingerprint` (varchar(64)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | article |
| `article_data_mime` | BASE TABLE | 924666 | `id` (bigint(20)), `article_id` (bigint(20)), `a_from` (mediumtext), `a_reply_to` (mediumtext), `a_to` (mediumtext), `a_cc` (mediumtext), `a_bcc` (mediumtext), `a_subject` (text), `a_message_id` (text), `a_message_id_md5` (varchar(32)), `a_in_reply_to` (mediumtext), `a_references` (mediumtext), `a_content_type` (varchar(250)), `incoming_time` (int(11)), `content_path` (varchar(250)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | article |
| `article_data_mime_attachment` | BASE TABLE | 1456 | `id` (bigint(20)), `article_id` (bigint(20)), `filename` (varchar(250)), `content_size` (varchar(30)), `content_type` (text), `content_id` (varchar(250)), `content_alternative` (varchar(50)), `disposition` (varchar(15)), `content` (longblob), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | article |
| `article_data_mime_plain` | BASE TABLE | 558 | `id` (bigint(20)), `article_id` (bigint(20)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | article |
| `article_data_mime_send_error` | BASE TABLE | 2007 | `id` (bigint(20)), `article_id` (bigint(20)), `message_id` (varchar(200)), `log_message` (mediumtext), `create_time` (datetime) | article |
| `article_data_otrs_chat` | BASE TABLE | 0 | `id` (bigint(20)), `article_id` (bigint(20)), `chat_participant_id` (varchar(255)), `chat_participant_name` (varchar(255)), `chat_participant_type` (varchar(255)), `message_text` (text), `system_generated` (smallint(6)), `create_time` (datetime) | article |
| `article_flag` | BASE TABLE | 2800468 | `article_id` (bigint(20)), `article_key` (varchar(50)), `article_value` (varchar(50)), `create_time` (datetime), `create_by` (int(11)) | article |
| `article_search_index` | BASE TABLE | 2928427 | `id` (bigint(20)), `ticket_id` (bigint(20)), `article_id` (bigint(20)), `article_key` (varchar(200)), `article_value` (mediumtext) | article |
| `article_sender_type` | BASE TABLE | 4 | `id` (smallint(6)), `name` (varchar(200)), `comments` (varchar(250)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | type, article |
| `auto_response_type` | BASE TABLE | 5 | `id` (smallint(6)), `name` (varchar(200)), `comments` (varchar(250)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | type |
| `calendar_appointment_ticket` | BASE TABLE | 0 | `calendar_id` (bigint(20)), `ticket_id` (bigint(20)), `rule_id` (varchar(32)), `appointment_id` (bigint(20)) | ticket |
| `cloud_service_config` | BASE TABLE | 0 | `id` (int(11)), `name` (varchar(200)), `config` (longblob), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | service, config |
| `communication_log_object` | BASE TABLE | 5497 | `id` (bigint(20)), `insert_fingerprint` (varchar(64)), `communication_id` (bigint(20)), `object_type` (varchar(50)), `status` (varchar(200)), `start_time` (datetime), `end_time` (datetime) | object |
| `communication_log_object_entry` | BASE TABLE | 71487 | `id` (bigint(20)), `communication_log_object_id` (bigint(20)), `log_key` (varchar(200)), `log_value` (mediumtext), `priority` (varchar(50)), `create_time` (datetime) | object |
| `configitem` | BASE TABLE | 11166 | `id` (bigint(20)), `configitem_number` (varchar(100)), `class_id` (int(11)), `last_version_id` (bigint(20)), `cur_depl_state_id` (int(11)), `cur_inci_state_id` (int(11)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | config, item |
| `configitem_counter` | BASE TABLE | 13 | `class_id` (int(11)), `counter_type` (varchar(50)), `counter` (varchar(50)) | config, item |
| `configitem_definition` | BASE TABLE | 30 | `id` (int(11)), `class_id` (int(11)), `configitem_definition` (longblob), `version` (int(11)), `create_time` (datetime), `create_by` (int(11)) | config, item |
| `configitem_history` | BASE TABLE | 175000 | `id` (bigint(20)), `configitem_id` (bigint(20)), `type_id` (int(11)), `content` (varchar(255)), `create_time` (datetime), `create_by` (int(11)) | history, config, item |
| `configitem_history_type` | BASE TABLE | 13 | `id` (int(11)), `name` (varchar(255)), `comments` (varchar(255)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | type, history, config, item |
| `configitem_version` | BASE TABLE | 31001 | `id` (bigint(20)), `configitem_id` (bigint(20)), `name` (varchar(250)), `definition_id` (int(11)), `depl_state_id` (int(11)), `inci_state_id` (int(11)), `create_time` (datetime), `create_by` (int(11)) | config, item |
| `customer_company` | BASE TABLE | 1268 | `customer_id` (varchar(150)), `name` (varchar(200)), `street` (varchar(200)), `zip` (varchar(200)), `city` (varchar(200)), `country` (varchar(200)), `url` (varchar(200)), `comments` (varchar(250)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)), `email` (varchar(250)), `country_state` (varchar(250)), `address` (varchar(250)), `contact` (varchar(250)) | customer |
| `customer_portal_cards` | BASE TABLE | 2 | `id` (int(11)), `name` (varchar(200)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | customer |
| `customer_portal_inquiry_forms` | BASE TABLE | 0 | `id` (int(11)), `name` (text), `service_form_id` (varchar(11)), `config` (longblob), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | customer |
| `customer_portal_notifications` | BASE TABLE | 0 | `id` (int(11)), `customer_user_login` (varchar(200)), `article_id` (bigint(20)), `is_read` (int(11)) | customer |
| `customer_preferences` | BASE TABLE | 3634 | `user_id` (varchar(250)), `preferences_key` (varchar(150)), `preferences_value` (varchar(250)) | customer |
| `customer_user` | BASE TABLE | 641 | `id` (int(11)), `login` (varchar(200)), `email` (varchar(150)), `customer_id` (varchar(150)), `title` (varchar(50)), `first_name` (varchar(100)), `last_name` (varchar(100)), `phone` (varchar(150)), `fax` (varchar(150)), `mobile` (varchar(150)), `street` (varchar(150)), `zip` (varchar(200)), `city` (varchar(200)), `country` (varchar(200)), `comments` (varchar(250)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | customer |
| `customer_user_azure_mirror` | BASE TABLE | 0 | `id` (int(11)), `tenant_count` (varchar(5)), `login` (varchar(200)), `email` (varchar(150)), `customer_id` (varchar(150)), `first_name` (varchar(100)), `last_name` (varchar(100)), `fullname` (varchar(250)), `display_name` (varchar(250)), `mail_string` (text), `user_data` (text), `valid_id` (smallint(6)), `create_time` (datetime), `change_time` (datetime) | customer |
| `customer_user_customer` | BASE TABLE | 0 | `user_id` (varchar(100)), `customer_id` (varchar(150)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | customer |
| `custom_form_dynamic_field` | BASE TABLE | 2 | `form_id` (int(11)), `field_id` (int(11)), `description` (varchar(200)), `mask` (varchar(50)), `default_value` (varchar(50)), `display` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | dynamic, field |
| `custom_form_service` | BASE TABLE | 308 | `form_id` (int(11)), `service_id` (int(11)) | service |
| `dynamic_field` | BASE TABLE | 221 | `id` (int(11)), `internal_field` (smallint(6)), `name` (varchar(200)), `label` (varchar(200)), `field_order` (int(11)), `field_type` (varchar(200)), `object_type` (varchar(100)), `config` (longblob), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | dynamic, field |
| `dynamic_field_obj_id_name` | BASE TABLE | 8 | `object_id` (int(11)), `object_name` (varchar(200)), `object_type` (varchar(100)) | dynamic, field |
| `dynamic_field_value` | BASE TABLE | 1543216 | `id` (int(11)), `field_id` (int(11)), `object_id` (bigint(20)), `value_text` (text), `value_date` (datetime), `value_int` (bigint(20)) | dynamic, field |
| `faq_history` | BASE TABLE | 266 | `id` (int(11)), `name` (varchar(200)), `item_id` (int(11)), `created` (datetime), `created_by` (int(11)), `changed` (datetime), `changed_by` (int(11)) | history |
| `faq_item` | BASE TABLE | 53 | `id` (int(11)), `f_number` (varchar(200)), `f_subject` (varchar(200)), `f_name` (varchar(200)), `f_language_id` (smallint(6)), `state_id` (smallint(6)), `category_id` (smallint(6)), `approved` (smallint(6)), `valid_id` (smallint(6)), `content_type` (varchar(250)), `f_keywords` (text), `f_field1` (text), `f_field2` (text), `f_field3` (text), `f_field4` (text), `f_field5` (text), `f_field6` (text), `created` (datetime), `created_by` (int(11)), `changed` (datetime), `changed_by` (int(11)) | item |
| `faq_state` | BASE TABLE | 3 | `id` (smallint(6)), `name` (varchar(200)), `type_id` (int(11)) | state |
| `faq_state_type` | BASE TABLE | 3 | `id` (smallint(6)), `name` (varchar(200)) | state, type |
| `general_catalog` | BASE TABLE | 138 | `id` (int(11)), `general_catalog_class` (varchar(100)), `name` (varchar(100)), `valid_id` (smallint(6)), `comments` (varchar(200)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | catalog |
| `general_catalog_preferences` | BASE TABLE | 50 | `general_catalog_id` (int(11)), `pref_key` (varchar(255)), `pref_value` (varchar(255)) | catalog |
| `gi_webservice_config` | BASE TABLE | 7 | `id` (int(11)), `name` (varchar(200)), `config` (longblob), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | service, config |
| `gi_webservice_config_history` | BASE TABLE | 43 | `id` (bigint(20)), `config_id` (int(11)), `config` (longblob), `config_md5` (varchar(32)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | service, history, config |
| `group_customer` | BASE TABLE | 0 | `customer_id` (varchar(150)), `group_id` (int(11)), `permission_key` (varchar(20)), `permission_value` (smallint(6)), `permission_context` (varchar(100)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | customer |
| `group_customer_user` | BASE TABLE | 0 | `user_id` (varchar(100)), `group_id` (int(11)), `permission_key` (varchar(20)), `permission_value` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | customer |
| `imexport_mapping_object` | BASE TABLE | 153 | `id` (bigint(20)), `mapping_id` (bigint(20)), `data_key` (varchar(100)), `data_value` (varchar(200)) | object |
| `imexport_object` | BASE TABLE | 25 | `id` (bigint(20)), `template_id` (bigint(20)), `data_key` (varchar(100)), `data_value` (varchar(200)) | object |
| `link_object` | BASE TABLE | 5 | `id` (smallint(6)), `name` (varchar(100)) | link, object |
| `link_relation` | BASE TABLE | 25411 | `source_object_id` (smallint(6)), `source_key` (varchar(50)), `target_object_id` (smallint(6)), `target_key` (varchar(50)), `type_id` (smallint(6)), `state_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)) | link, relation |
| `link_state` | BASE TABLE | 2 | `id` (smallint(6)), `name` (varchar(50)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | state, link |
| `link_type` | BASE TABLE | 7 | `id` (smallint(6)), `name` (varchar(50)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | type, link |
| `mail_queue` | BASE TABLE | 0 | `id` (bigint(20)), `insert_fingerprint` (varchar(64)), `article_id` (bigint(20)), `attempts` (int(11)), `sender` (varchar(200)), `recipient` (mediumtext), `raw_message` (longblob), `due_time` (datetime), `last_smtp_code` (int(11)), `last_smtp_message` (mediumtext), `create_time` (datetime) | queue |
| `notification_event_item` | BASE TABLE | 820 | `notification_id` (int(11)), `event_key` (varchar(200)), `event_value` (varchar(200)) | item |
| `oauth2_token_config` | BASE TABLE | 0 | `id` (int(11)), `name` (varchar(250)), `config` (text), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)), `dbcrud_uuid` (varchar(36)) | config |
| `personal_queues` | BASE TABLE | 80 | `user_id` (int(11)), `queue_id` (int(11)) | queue |
| `personal_services` | BASE TABLE | 0 | `user_id` (int(11)), `service_id` (int(11)) | service |
| `queue` | BASE TABLE | 180 | `id` (int(11)), `name` (varchar(200)), `group_id` (int(11)), `unlock_timeout` (int(11)), `first_response_time` (int(11)), `first_response_notify` (smallint(6)), `update_time` (int(11)), `update_notify` (smallint(6)), `solution_time` (int(11)), `solution_notify` (smallint(6)), `system_address_id` (smallint(6)), `calendar_name` (varchar(100)), `default_sign_key` (varchar(100)), `salutation_id` (smallint(6)), `signature_id` (smallint(6)), `follow_up_id` (smallint(6)), `follow_up_lock` (smallint(6)), `comments` (varchar(250)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | queue |
| `queue_auto_response` | BASE TABLE | 95 | `id` (int(11)), `queue_id` (int(11)), `auto_response_id` (int(11)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | queue |
| `queue_preferences` | BASE TABLE | 0 | `queue_id` (int(11)), `preferences_key` (varchar(150)), `preferences_value` (varchar(250)) | queue |
| `queue_standard_template` | BASE TABLE | 217 | `queue_id` (int(11)), `standard_template_id` (int(11)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | queue |
| `service` | BASE TABLE | 411 | `id` (int(11)), `name` (varchar(200)), `valid_id` (smallint(6)), `comments` (varchar(250)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)), `type_id` (int(11)), `criticality` (varchar(200)) | service |
| `service_customer_user` | BASE TABLE | 440 | `customer_user_login` (varchar(200)), `service_id` (int(11)), `create_time` (datetime), `create_by` (int(11)) | service, customer |
| `service_preferences` | BASE TABLE | 3926 | `service_id` (int(11)), `preferences_key` (varchar(150)), `preferences_value` (text) | service |
| `service_sla` | BASE TABLE | 173 | `service_id` (int(11)), `sla_id` (int(11)) | service, sla |
| `sla` | BASE TABLE | 41 | `id` (int(11)), `name` (varchar(200)), `calendar_name` (varchar(100)), `first_response_time` (int(11)), `first_response_notify` (smallint(6)), `update_time` (int(11)), `update_notify` (smallint(6)), `solution_time` (int(11)), `solution_notify` (smallint(6)), `valid_id` (smallint(6)), `comments` (varchar(250)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)), `type_id` (int(11)), `min_time_bet_incidents` (int(11)) | sla |
| `sla_preferences` | BASE TABLE | 0 | `sla_id` (int(11)), `preferences_key` (varchar(150)), `preferences_value` (varchar(250)) | sla |
| `smime_signer_cert_relations` | BASE TABLE | 0 | `id` (int(11)), `cert_hash` (varchar(8)), `cert_fingerprint` (varchar(59)), `ca_hash` (varchar(8)), `ca_fingerprint` (varchar(59)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | relation |
| `survey_queue` | BASE TABLE | 42 | `survey_id` (bigint(20)), `queue_id` (int(11)) | queue |
| `sysconfig_default` | BASE TABLE | 3248 | `id` (int(11)), `name` (varchar(250)), `description` (longblob), `navigation` (varchar(200)), `is_invisible` (smallint(6)), `is_readonly` (smallint(6)), `is_required` (smallint(6)), `is_valid` (smallint(6)), `has_configlevel` (smallint(6)), `user_modification_possible` (smallint(6)), `user_modification_active` (smallint(6)), `user_preferences_group` (varchar(250)), `xml_content_raw` (longblob), `xml_content_parsed` (longblob), `xml_filename` (varchar(250)), `effective_value` (longblob), `is_dirty` (smallint(6)), `exclusive_lock_guid` (varchar(32)), `exclusive_lock_user_id` (int(11)), `exclusive_lock_expiry_time` (datetime), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | config |
| `sysconfig_default_version` | BASE TABLE | 3202 | `id` (int(11)), `sysconfig_default_id` (int(11)), `name` (varchar(250)), `description` (longblob), `navigation` (varchar(200)), `is_invisible` (smallint(6)), `is_readonly` (smallint(6)), `is_required` (smallint(6)), `is_valid` (smallint(6)), `has_configlevel` (smallint(6)), `user_modification_possible` (smallint(6)), `user_modification_active` (smallint(6)), `user_preferences_group` (varchar(250)), `xml_content_raw` (longblob), `xml_content_parsed` (longblob), `xml_filename` (varchar(250)), `effective_value` (longblob), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | config |
| `sysconfig_deployment` | BASE TABLE | 21 | `id` (int(11)), `comments` (varchar(250)), `user_id` (int(11)), `effective_value` (longblob), `create_time` (datetime), `create_by` (int(11)) | config |
| `sysconfig_deployment_lock` | BASE TABLE | 0 | `id` (int(11)), `exclusive_lock_guid` (varchar(32)), `exclusive_lock_user_id` (int(11)), `exclusive_lock_expiry_time` (datetime) | config |
| `sysconfig_modified` | BASE TABLE | 252 | `id` (int(11)), `sysconfig_default_id` (int(11)), `name` (varchar(250)), `user_id` (int(11)), `is_valid` (smallint(6)), `user_modification_active` (smallint(6)), `effective_value` (longblob), `is_dirty` (smallint(6)), `reset_to_default` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | config |
| `sysconfig_modified_version` | BASE TABLE | 487 | `id` (int(11)), `sysconfig_default_version_id` (int(11)), `name` (varchar(250)), `user_id` (int(11)), `is_valid` (smallint(6)), `user_modification_active` (smallint(6)), `effective_value` (longblob), `reset_to_default` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | config |
| `temp_config_item` | BASE TABLE | 2814 | `field_id` (int(11)), `config_item_id` (int(11)), `field_name` (text), `field_config_item_value` (text) | config, item |
| `temp_select_config_item` | BASE TABLE | 16095 | `field_id` (int(11)), `config_item_id` (int(11)), `name` (text), `customer_id` (varchar(150)), `data` (text) | config, item |
| `ticket` | BASE TABLE | 106546 | `id` (bigint(20)), `tn` (varchar(50)), `title` (varchar(255)), `queue_id` (int(11)), `ticket_lock_id` (smallint(6)), `type_id` (smallint(6)), `service_id` (int(11)), `sla_id` (int(11)), `user_id` (int(11)), `responsible_user_id` (int(11)), `ticket_priority_id` (smallint(6)), `ticket_state_id` (smallint(6)), `customer_id` (varchar(150)), `customer_user_id` (varchar(250)), `timeout` (int(11)), `until_time` (int(11)), `escalation_time` (int(11)), `escalation_update_time` (int(11)), `escalation_response_time` (int(11)), `escalation_solution_time` (int(11)), `archive_flag` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | ticket |
| `ticket_flag` | BASE TABLE | 77711 | `ticket_id` (bigint(20)), `ticket_key` (varchar(50)), `ticket_value` (varchar(50)), `create_time` (datetime), `create_by` (int(11)) | ticket |
| `ticket_history` | BASE TABLE | 7689816 | `id` (bigint(20)), `name` (varchar(200)), `history_type_id` (smallint(6)), `ticket_id` (bigint(20)), `article_id` (bigint(20)), `type_id` (smallint(6)), `queue_id` (int(11)), `owner_id` (int(11)), `priority_id` (smallint(6)), `state_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | ticket, history |
| `ticket_history_type` | BASE TABLE | 51 | `id` (smallint(6)), `name` (varchar(200)), `comments` (varchar(250)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | ticket, type, history |
| `ticket_index` | BASE TABLE | 0 | `ticket_id` (bigint(20)), `queue_id` (int(11)), `queue` (varchar(200)), `group_id` (int(11)), `s_lock` (varchar(200)), `s_state` (varchar(200)), `create_time` (datetime) | ticket |
| `ticket_invoker_oauth` | BASE TABLE | 0 | `web_service` (varchar(200)), `invoker` (varchar(200)), `expiration_tate` (datetime) | ticket |
| `ticket_lock_index` | BASE TABLE | 0 | `ticket_id` (bigint(20)) | ticket |
| `ticket_lock_type` | BASE TABLE | 3 | `id` (smallint(6)), `name` (varchar(200)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | ticket, type |
| `ticket_loop_protection` | BASE TABLE | 43 | `sent_to` (varchar(250)), `sent_date` (varchar(150)) | ticket |
| `ticket_number_counter` | BASE TABLE | 10 | `id` (bigint(20)), `counter` (bigint(20)), `counter_uid` (varchar(32)), `create_time` (datetime) | ticket |
| `ticket_priority` | BASE TABLE | 5 | `id` (smallint(6)), `name` (varchar(200)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | ticket, priority |
| `ticket_state` | BASE TABLE | 31 | `id` (smallint(6)), `name` (varchar(200)), `comments` (varchar(250)), `type_id` (smallint(6)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | ticket, state |
| `ticket_state_type` | BASE TABLE | 7 | `id` (smallint(6)), `name` (varchar(200)), `comments` (varchar(250)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | ticket, state, type |
| `ticket_type` | BASE TABLE | 4 | `id` (smallint(6)), `name` (varchar(200)), `valid_id` (smallint(6)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | ticket, type |
| `ticket_watcher` | BASE TABLE | 0 | `ticket_id` (bigint(20)), `user_id` (int(11)), `create_time` (datetime), `create_by` (int(11)), `change_time` (datetime), `change_by` (int(11)) | ticket |

## Índices de `ticket`

| Índice | Coluna | Único |
|---|---|---|
| `FK_ticket_change_by_id` | `change_by` | não |
| `FK_ticket_create_by_id` | `create_by` | não |
| `FK_ticket_service_id_id` | `service_id` | não |
| `FK_ticket_sla_id_id` | `sla_id` | não |
| `PRIMARY` | `id` | sim |
| `ticket_archive_flag` | `archive_flag` | não |
| `ticket_create_time` | `create_time` | não |
| `ticket_customer_id` | `customer_id` | não |
| `ticket_customer_user_id` | `customer_user_id` | não |
| `ticket_escalation_response_time` | `escalation_response_time` | não |
| `ticket_escalation_solution_time` | `escalation_solution_time` | não |
| `ticket_escalation_time` | `escalation_time` | não |
| `ticket_escalation_update_time` | `escalation_update_time` | não |
| `ticket_queue_id` | `queue_id` | não |
| `ticket_queue_view` | `ticket_state_id` | não |
| `ticket_queue_view` | `ticket_lock_id` | não |
| `ticket_responsible_user_id` | `responsible_user_id` | não |
| `ticket_ticket_lock_id` | `ticket_lock_id` | não |
| `ticket_ticket_priority_id` | `ticket_priority_id` | não |
| `ticket_ticket_state_id` | `ticket_state_id` | não |
| `ticket_timeout` | `timeout` | não |
| `ticket_title` | `title` | não |
| `ticket_tn` | `tn` | sim |
| `ticket_type_id` | `type_id` | não |
| `ticket_until_time` | `until_time` | não |
| `ticket_user_id` | `user_id` | não |

## Índices de `link_relation`

| Índice | Coluna | Único |
|---|---|---|
| `FK_link_relation_create_by_id` | `create_by` | não |
| `FK_link_relation_state_id_id` | `state_id` | não |
| `FK_link_relation_type_id_id` | `type_id` | não |
| `link_relation_list_source` | `source_object_id` | não |
| `link_relation_list_source` | `source_key` | não |
| `link_relation_list_source` | `state_id` | não |
| `link_relation_list_target` | `target_object_id` | não |
| `link_relation_list_target` | `target_key` | não |
| `link_relation_list_target` | `state_id` | não |
| `link_relation_view` | `source_object_id` | sim |
| `link_relation_view` | `source_key` | sim |
| `link_relation_view` | `target_object_id` | sim |
| `link_relation_view` | `target_key` | sim |
| `link_relation_view` | `type_id` | sim |

## Chaves estrangeiras declaradas

| Tabela | Coluna | Referência |
|---|---|---|
| `ticket` | `change_by` | `users.id` |
| `ticket` | `create_by` | `users.id` |
| `ticket` | `queue_id` | `queue.id` |
| `ticket` | `responsible_user_id` | `users.id` |
| `ticket` | `service_id` | `service.id` |
| `ticket` | `sla_id` | `sla.id` |
| `ticket` | `ticket_lock_id` | `ticket_lock_type.id` |
| `ticket` | `ticket_priority_id` | `ticket_priority.id` |
| `ticket` | `ticket_state_id` | `ticket_state.id` |
| `ticket` | `type_id` | `ticket_type.id` |
| `ticket` | `user_id` | `users.id` |
| `link_relation` | `create_by` | `users.id` |
| `link_relation` | `source_object_id` | `link_object.id` |
| `link_relation` | `state_id` | `link_state.id` |
| `link_relation` | `target_object_id` | `link_object.id` |
| `link_relation` | `type_id` | `link_type.id` |
