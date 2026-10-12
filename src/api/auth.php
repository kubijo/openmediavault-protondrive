<?php

/** Validate OMV's own session for nginx's internal authentication subrequest. */

try {
    require_once 'openmediavault/autoloader.inc';
    require_once 'openmediavault/env.inc';
    require_once 'openmediavault/functions.inc';
    $models = \OMV\DataModel\Manager::getInstance();
    $models->load();
    $session = \OMV\Session::getInstance();
    $session->start();
    $session->validate();
    if ($session->getRole() !== OMV_ROLE_ADMINISTRATOR) {
        http_response_code(403);
    } else {
        if (($_SERVER['HTTP_X_PROTONDRIVE_ACTIVITY'] ?? '') === '1') {
            $session->updateLastAccess();
        }
        header('X-Protondrive-User: ' . $session->getUsername());
        http_response_code(204);
    }
    // Polling and streams must not extend an inactive login. OMV itself remains
    // responsible for session creation, expiration and logout.
    $session->commit();
} catch (\Exception $error) {
    http_response_code($error instanceof \OMV\BaseException ? $error->getHttpStatusCode() : 500);
}
